"""在某个观察时点（cutoff）上，为每位会员计算特征快照。

只使用 cutoff 当时及之前的订单，避免"用未来预测未来"。
同一函数既用于训练（历史 cutoff + 未来标签），也用于当前打分（cutoff = 数据最后一天）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import CONFIG

ACTIVITY_WINDOWS = CONFIG.activity_windows   # 近 N 天活跃度
TREND_WINDOW = CONFIG.trend_window           # 消费趋势：近 N 天 vs 之前 N 天


def build_snapshot(orders: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    hist = orders[orders["order_time"] <= cutoff]
    day = hist["order_time"].dt.normalize()
    hist = hist.assign(day=day, age_days=(cutoff - hist["order_time"]).dt.days)

    g = hist.groupby("customer_id")
    snap = pd.DataFrame({
        "first_order": g["order_time"].min(),
        "last_order": g["order_time"].max(),
        "frequency": g["order_id"].nunique(),
        "monetary": g["amount"].sum(),
        "aov": g["amount"].mean(),
        "avg_skus": g["n_skus"].mean(),
        "n_days": g["day"].nunique(),
    })
    # 价格/券敏感度：优惠占原价比例、用券订单占比
    if "discount" in hist.columns:
        snap["discount_rate"] = g["discount"].sum() / (snap["monetary"] + g["discount"].sum())
    if "coupon_disc" in hist.columns:
        snap["coupon_order_share"] = g["coupon_disc"].apply(lambda s: (s > 0).mean())

    snap["recency_days"] = (cutoff - snap["last_order"]).dt.days
    snap["tenure_days"] = (cutoff - snap["first_order"]).dt.days

    # 近期活跃度与消费趋势
    for w in sorted(set(ACTIVITY_WINDOWS) | {TREND_WINDOW}):
        recent = hist[hist["age_days"] < w].groupby("customer_id")
        snap[f"orders_{w}d"] = recent["order_id"].nunique()
        snap[f"amount_{w}d"] = recent["amount"].sum()
    prev = hist[(hist["age_days"] >= TREND_WINDOW) & (hist["age_days"] < 2 * TREND_WINDOW)].groupby("customer_id")
    snap["amount_prev"] = prev["amount"].sum()
    snap = snap.fillna({c: 0 for c in snap.columns if c.startswith(("orders_", "amount_"))})
    snap["spend_trend"] = np.log1p(snap[f"amount_{TREND_WINDOW}d"]) - np.log1p(snap["amount_prev"])

    # 购买节奏：平均复购间隔，以及"当前沉默时长 / 平均间隔"
    active_span = (snap["last_order"] - snap["first_order"]).dt.days
    snap["mean_gap_days"] = np.where(snap["n_days"] > 1, active_span / (snap["n_days"] - 1), np.nan)
    # 只有一个间隔时（2 个消费日）间隔不稳定，相邻两天消费会得出"沉默 N 倍"的假信号
    snap["recency_gap_ratio"] = (snap["recency_days"] / snap["mean_gap_days"]).where(snap["n_days"] >= 3)

    # BG/NBD & Gamma-Gamma 输入（以周为单位，同一天多单算一次购买）
    snap["bg_x"] = snap["n_days"] - 1
    snap["bg_t_x"] = active_span / 7.0
    snap["bg_T"] = snap["tenure_days"] / 7.0
    # Gamma-Gamma 的金额口径必须与 BG/NBD 的"购买"一致：每个消费日的金额，不含首日
    daily = hist.groupby(["customer_id", "day"])["amount"].sum()
    first_day_amt = daily.groupby(level="customer_id").first()
    snap["gg_monetary"] = (snap["monetary"] - first_day_amt) / snap["bg_x"].replace(0, np.nan)

    snap["cutoff"] = cutoff
    snap.index.name = "customer_id"
    return snap


def future_labels(orders: pd.DataFrame, cutoff: pd.Timestamp, horizon_days: int) -> pd.DataFrame:
    """cutoff 之后 horizon_days 天内每位会员的真实购买情况（训练/验证用标签）。"""
    end = cutoff + pd.Timedelta(days=horizon_days)
    fut = orders[(orders["order_time"] > cutoff) & (orders["order_time"] <= end)]
    g = fut.groupby("customer_id")
    return pd.DataFrame({
        "future_orders": g["order_time"].apply(lambda s: s.dt.normalize().nunique()),
        "future_amount": g["amount"].sum(),
    })

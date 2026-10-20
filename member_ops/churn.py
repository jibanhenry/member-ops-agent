"""流失预警：预测"未来 N 天不再购买"的概率，并给出可读的流失原因。

训练方式（时间切分，严格不泄漏未来信息）：
    T1 = 数据末日 - 2N  -> 特征@T1，标签 = (T1, T1+N] 是否购买   [训练集]
    T2 = 数据末日 - N   -> 特征@T2，标签 = (T2, 末日] 是否购买   [验证集，报告指标]
    最终模型用 T1+T2 合并重训，在末日打分。
只对"仍在场"的会员建模：观察时点前 ACTIVE_WINDOW_DAYS 天内有过购买。
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from .data import CONFIG

BASE_FEATURES = [
    "recency_days", "tenure_days", "frequency", "monetary", "aov", "avg_skus",
    "amount_prev", "spend_trend", "mean_gap_days", "recency_gap_ratio",
    "discount_rate", "coupon_order_share",   # 会员系统有优惠记录时才有
]
ACTIVE_WINDOW_DAYS = CONFIG.active_window_days


def feature_cols(snap: pd.DataFrame) -> list[str]:
    windows = [c for c in snap.columns if re.fullmatch(r"(orders|amount)_\d+d", c)]
    return [c for c in BASE_FEATURES if c in snap.columns] + windows


def in_play(snap: pd.DataFrame) -> pd.DataFrame:
    return snap[snap["recency_days"] <= ACTIVE_WINDOW_DAYS]


def make_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40,
        l2_regularization=1.0, random_state=42,
    )


def churn_label(snap: pd.DataFrame, labels: pd.DataFrame) -> pd.Series:
    bought = labels["future_orders"].reindex(snap.index).fillna(0) > 0
    return (~bought).astype(int)


def evaluate(model, X: pd.DataFrame, y: pd.Series) -> dict:
    p = model.predict_proba(X)[:, 1]
    order = np.argsort(-p)
    top = order[: max(1, len(order) // 10)]
    base = y.mean()
    return {
        "n": int(len(y)),
        "churn_rate": round(float(base), 4),
        "auc": round(float(roc_auc_score(y, p)), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "top10_precision": round(float(y.iloc[top].mean()), 4),
        "top10_lift": round(float(y.iloc[top].mean() / base), 2),
    }


def reason_codes(row: pd.Series) -> list[str]:
    """把模型输入翻译成运营人员能直接理解的流失信号。"""
    reasons = []
    if row["frequency"] == 1:
        reasons.append("只买过 1 次，还没形成复购习惯")
    elif row["n_days"] == 1:
        reasons.append("所有消费都集中在同一天，还没形成回店习惯")
    ratio = row.get("recency_gap_ratio")
    if pd.notna(ratio) and ratio >= 2:
        reasons.append(f"已沉默 {int(row['recency_days'])} 天，是其平均复购间隔的 {ratio:.1f} 倍")
    q, t = CONFIG.quiet_window, CONFIG.trend_window
    if row[f"orders_{q}d"] == 0 and row["frequency"] > 1:
        reasons.append(f"近 {q} 天没有任何消费")
    if row["amount_prev"] > 0 and row["spend_trend"] < -0.7:
        drop = 1 - row[f"amount_{t}d"] / row["amount_prev"]
        reasons.append(f"近 {t} 天消费比之前 {t} 天下降 {drop:.0%}")
    if row.get("p_alive", 1) < 0.5:
        reasons.append(f"购买行为模型估计仍活跃的概率仅 {row['p_alive']:.0%}")
    prob = row.get("churn_prob")
    if not reasons and pd.notna(prob) and prob >= 0.5:
        reasons.append(f"流失模型综合判断 {CONFIG.churn_horizon_days} 天内不再消费的概率为 {prob:.0%}")
    return reasons

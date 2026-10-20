"""行动编排：把"这类人用什么策略"细化成"这个人下一步做什么、什么时候做、给什么权益、最多花多少钱"。

- 补货节奏：消费天数够多用会员自己的平均间隔，否则用主力品牌的同客复购间隔中位数
- 权益选择：高价值会员不做价格减免；羊毛党只在算得过账时才给让利；其余按优惠敏感度区分
- 成本上限：锚定预测未来一年消费，而不是历史消费
- 触达编排：每人同一时间只有一个下一步动作；人工回访受导购产能约束、按风险金额排队；
  付费没达到预期的会员优先定向触达；临近大促的自动触达并入大促统一推送
规则全部来自 playbook.yaml，缺省时用 DEFAULTS。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .segments import match_strategy

DEFAULTS = {
    "replenishment": {"personal_min_days": 3, "remind_at": 0.75, "window_days": 14,
                      "min_cycle_days": 14, "max_cycle_days": 180, "brand_min_pairs": 200},
    "incentive_rules": {"price_sensitive_discount_rate": 0.02, "price_sensitive_coupon_share": 0.3,
                        "deal_hunter_promo_share": 0.8, "no_price_cut_tiers": ["high"]},
    "contact_policy": {"max_touches_per_week": 1, "max_touches_per_month": 3, "manual_capacity_per_week": 300,
                       "manual_max_weeks": 4, "promo_quiet_days": 14,
                       "manual_stage_priority": ["at_risk", "one_time", "growing", "dormant"]},
}

ACTION_NAMES = {
    "manual": "本周人工回访",
    "manual_queue": "排队等人工回访",
    "shortfall": "未达预期定向触达",
    "replenish": "补货提醒",
    "promo_merge": "并入大促推送",
    "auto": "自动触达",
    "wait": "暂不打扰",
    "promo_only": "仅大促触达",
    "holdout": "对照组·不触达",
}

# 补货窗口只对还在消费周期里的会员有意义
REPLENISH_STAGES = {"new", "one_time", "growing", "mature", "at_risk"}


def cfg(pb: dict, section: str) -> dict:
    return DEFAULTS[section] | (pb.get(section) or {})


# ---------- 补货节奏 ----------

def brand_cycles(orders: pd.DataFrame, min_pairs: int) -> tuple[pd.Series, float | None]:
    """每个品牌的同客复购间隔中位数（天），以及全部品牌合计的中位数。没有品牌字段时返回空。"""
    if "brand" not in orders.columns:
        return pd.Series(dtype=float), None
    o = (orders.assign(day=orders["order_time"].dt.normalize())[["customer_id", "brand", "day"]]
         .drop_duplicates().sort_values("day"))
    o["gap"] = o.groupby(["customer_id", "brand"])["day"].diff().dt.days
    gaps = o.dropna(subset=["gap"])
    g = gaps.groupby("brand")["gap"].agg(["median", "size"])
    return g.loc[g["size"] >= min_pairs, "median"], float(gaps["gap"].median()) if len(gaps) else None


def purchase_cycle(m: pd.DataFrame, brand_cycle: pd.Series, overall: float | None,
                   cutoff: pd.Timestamp, pb: dict) -> pd.DataFrame:
    c = cfg(pb, "replenishment")
    personal = m["mean_gap_days"].where(m["n_days"] >= c["personal_min_days"])
    brand = m["top_brand"].map(brand_cycle) if "top_brand" in m.columns else pd.Series(np.nan, index=m.index)
    fallback = overall if overall is not None else m["mean_gap_days"].median()
    cycle = personal.fillna(brand).fillna(fallback).clip(c["min_cycle_days"], c["max_cycle_days"]).round()
    source = np.select([personal.notna(), brand.notna()], ["个人节奏", "品牌周期"], "整体中位")

    remind = m["last_order"].dt.normalize() + pd.to_timedelta(cycle * c["remind_at"], unit="D")
    days_to = (remind - cutoff).dt.days
    status = pd.Series(np.select([days_to > c["window_days"], days_to >= -cycle * 0.5],
                                 ["未到", "补货窗口"], "已过补货期"), index=m.index)
    applicable = m["stage"].isin(REPLENISH_STAGES)
    return pd.DataFrame({
        "cycle_days": cycle,
        "cycle_source": source,
        "replenish_date": remind.where(applicable),
        "replenish_status": status.where(applicable),
    }, index=m.index)


# ---------- 权益与成本 ----------

def price_sensitive(m: pd.DataFrame, pb: dict) -> pd.Series:
    c = cfg(pb, "incentive_rules")
    s = pd.Series(False, index=m.index)
    if "discount_rate" in m.columns:
        s |= m["discount_rate"].fillna(0) >= c["price_sensitive_discount_rate"]
    if "coupon_order_share" in m.columns:
        s |= m["coupon_order_share"].fillna(0) >= c["price_sensitive_coupon_share"]
    return s


INCENTIVE_GROUPS = {"no_price_cut": "高价值·不降价", "deal_hunter": "羊毛党", "price_sensitive": "价格敏感", "regular": "非价格敏感"}


def incentive_group(m: pd.DataFrame, pb: dict) -> pd.Series:
    """高价值层一律不降价；不是高价值、且几乎只在打折时购买的是羊毛党；其余按优惠敏感度区分。"""
    c = cfg(pb, "incentive_rules")
    no_cut = m["value_tier"].isin(c["no_price_cut_tiers"])
    promo = m["promo_order_share"].fillna(0) if "promo_order_share" in m.columns else pd.Series(0.0, index=m.index)
    deal = promo >= c["deal_hunter_promo_share"]
    return pd.Series(np.select([no_cut, deal, price_sensitive(m, pb)], ["no_price_cut", "deal_hunter", "price_sensitive"],
                               "regular"), index=m.index)


def pick_incentive(strategy: dict | None, group: str) -> str | None:
    if not strategy:
        return None
    if group == "deal_hunter":
        return strategy.get("incentive_deal_hunter") or "只给有门槛、算得过账的权益；发券回不了本就不发，只做内容和积分提醒"
    if group == "price_sensitive":
        return strategy.get("incentive_price_sensitive") or strategy.get("incentive")
    return strategy.get("incentive")


# ---------- 大促日历 ----------

def upcoming_events(cutoff: pd.Timestamp, pb: dict, horizon_days: int = 365) -> list[dict]:
    """未结束、且在 horizon 内开始的大促档期，按开始时间排序。"""
    out = {}
    for e in pb.get("calendar") or []:
        for year in (cutoff.year, cutoff.year + 1):
            start = pd.Timestamp(f"{year}-{e['start']}")
            end = pd.Timestamp(f"{year}-{e['end']}")
            if end < start:
                end += pd.DateOffset(years=1)
            if end > cutoff and start <= cutoff + pd.Timedelta(days=horizon_days):
                out.setdefault(e["name"], {"name": e["name"], "start": start, "end": end,
                                           "days_until": max(0, (start - cutoff).days)})
    return sorted(out.values(), key=lambda e: e["start"])


# ---------- 下一步动作 ----------

def plan_actions(m: pd.DataFrame, cutoff: pd.Timestamp, pb: dict) -> pd.DataFrame:
    """每位会员一个下一步动作：类型、说明、日期、渠道、单人成本上限。m 需已有 stage / value_tier /
    value_at_risk / pred_clv_365d / replenish_status / replenish_date / cycle_days，可选 shortfall 相关列。"""
    cp = cfg(pb, "contact_policy")
    combos = {k: match_strategy(*k, pb) or {} for k in set(zip(m["stage"], m["value_tier"]))}
    strat = pd.Series([combos[k] for k in zip(m["stage"], m["value_tier"])], index=m.index)
    channel = strat.map(lambda s: s.get("channel", "auto"))
    cost_cap = strat.map(lambda s: s.get("cost_cap_pct", 0.0)) * m["pred_clv_365d"].fillna(0)

    events = upcoming_events(cutoff, pb)
    soon = next((e for e in events if e["days_until"] <= cp["promo_quiet_days"]), None)
    next_event = events[0] if events else None

    # 人工回访先按阶段优先级（越早拦截越划算）、再按风险金额排队，超过产能的顺延到下几周，排不进去的转自动
    manual = m.loc[channel == "manual"]
    rank = {st: i for i, st in enumerate(cp["manual_stage_priority"])}
    manual = manual.assign(_p=manual["stage"].map(rank).fillna(len(rank)))
    order = manual.sort_values(["_p", "value_at_risk", "pred_clv_365d"], ascending=[True, False, False]).index
    week = pd.Series(np.arange(len(order)) // max(1, cp["manual_capacity_per_week"]) + 1, index=order)

    a_type, a_text, a_date = [], [], []
    for cid, r in m.iterrows():
        s, ch = strat[cid], channel[cid]
        name = s.get("name", "")
        w = week.get(cid)
        if ch == "promo_only" or r["stage"] == "lost":
            t, text, d = "promo_only", f"仅在「{next_event['name']}」触达" if next_event else "仅年度大促触达", \
                next_event["start"] if next_event else pd.NaT
        elif w is not None and w <= cp["manual_max_weeks"]:
            t = "manual" if w == 1 else "manual_queue"
            text = f"{name}：导购人工回访" + ("" if w == 1 else f"（排在第 {w} 周）")
            d = cutoff + pd.Timedelta(days=7 * (w - 1) + 1)
        elif r.get("shortfall") and r["stage"] in REPLENISH_STAGES:
            t = "shortfall"
            text = (f"未达预期（预期 {r['expected_purchases_cp']:.1f} 次，实际 {r['actual_purchases_cp']:.0f} 次）："
                    f"{(r['shortfall_reasons'] or '原因待回访').split('；')[0]}")
            d = cutoff + pd.Timedelta(days=1)
        elif r["replenish_status"] == "补货窗口":
            t, text, d = "replenish", f"补货提醒（{r['cycle_days']:.0f} 天周期）", max(r["replenish_date"], cutoff + pd.Timedelta(days=1))
        elif r["stage"] in ("at_risk", "dormant", "one_time"):
            if soon:
                t, text, d = "promo_merge", f"{name}：并入「{soon['name']}」推送", max(soon["start"], cutoff + pd.Timedelta(days=1))
            else:
                t, text, d = "auto", f"{name}：自动触达", cutoff + pd.Timedelta(days=1)
            if w is not None:
                text += "（人工产能不足，转自动）"
        elif r["stage"] == "new":
            t, text, d = "auto", f"{name}：按入会天数节点触达", cutoff + pd.Timedelta(days=1)
        elif r["replenish_status"] == "未到":
            t, text, d = "wait", "暂不打扰，到补货窗口再提醒", r["replenish_date"]
        else:
            t, text, d = "auto", f"{name}：自动触达", cutoff + pd.Timedelta(days=1)
        a_type.append(t)
        a_text.append(text)
        a_date.append(d)

    return pd.DataFrame({"channel": channel, "cost_cap": cost_cap.round(0), "action_type": a_type,
                         "next_action": a_text, "next_action_date": pd.to_datetime(a_date)}, index=m.index)


def seasonality(orders: pd.DataFrame) -> dict[int, float]:
    """各自然月收入占全年比例。数据窗口里各月出现的年数不同，先取每个月份的年均收入再归一。"""
    t = orders["order_time"]
    by_month = orders.groupby([t.dt.year, t.dt.month])["amount"].sum().groupby(level=1).mean()
    return {int(k): round(float(v), 4) for k, v in (by_month / by_month.sum()).items()}

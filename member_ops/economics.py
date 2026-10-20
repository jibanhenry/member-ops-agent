"""算账：用户 ROI =（这位会员预测会产生的毛利 − 花在他身上的真实运营成本）÷ 真实运营成本。

- 回报：付费预测模型给出的未来消费 × 该会员的实际毛利率。实付金额已经扣过折扣，
  所以常打折的会员毛利率更低：实际毛利率 = 1 − (1 − 标价毛利率) / (1 − 折扣率)
- 成本：一年内的真实运营成本，不是券面额 =
  固定成本（每位会员分摊的会员系统、积分、客服等）
  + 针对这个人的动作投入（下一步动作按类型计价：导购人工回访、定向触达、补货提醒、并入大促……）
  + 策略里的例行触达 + 权益（让利按面额全额、赠品按成本价）
- 羊毛党逐人判断：他自己的毛利抵不过发券成本，就不发券
- 运营有没有效果，不在 ROI 里假设，而是看实际消费和预测消费的差距（见 insights 的预期跟踪）
规则和成本假设来自 playbook.yaml 的 economics 与各策略的 budget，缺省时用 DEFAULTS。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .segments import match_strategy

DEFAULTS = {
    "gross_margin": 0.30,
    "gift_cost_ratio": 0.4,
    "fixed_cost_per_member": 20.0,
    "action_cost": {"manual": 30.0, "manual_queue": 30.0, "shortfall": 5.0, "replenish": 0.5, "promo_merge": 0.2,
                    "auto": 0.5, "wait": 0.0, "promo_only": 0.2, "holdout": 0.0},
    "routine_touch_cost": 0.5,
    "return_basis": "365d",
    "lifetime_years": 3,
    "annual_discount_rate": 0.10,
}
BUDGET_DEFAULTS = {"gift_value": 0.0, "price_cut_value": 0.0, "redemption_rate": 0.3, "touches": 1}
NO_COUPON = "不发券（毛利抵不过发券成本）"


def econ(pb: dict) -> dict:
    e = pb.get("economics") or {}
    return DEFAULTS | e | {"action_cost": DEFAULTS["action_cost"] | (e.get("action_cost") or {})}


def lifetime_value(pred: pd.DataFrame, years: int, rate: float) -> pd.Series:
    """按年折现的未来 N 年预测消费；pred 需有 pred_clv_{365*k}d 列。"""
    total, prev = 0.0, 0.0
    for k in range(1, years + 1):
        cum = pred[f"pred_clv_{365 * k}d"]
        total = total + (cum - prev) / (1 + rate) ** (k - 0.5)
        prev = cum
    return total


def effective_margin(m: pd.DataFrame, pb: dict) -> pd.Series:
    """实付金额上的毛利率：折扣越多，毛利率越低，打折到成本以下记为 0。"""
    gm = econ(pb)["gross_margin"]
    if "discount_rate" not in m.columns:
        return pd.Series(gm, index=m.index)
    d = m["discount_rate"].fillna(0).clip(0, 0.95)
    return (1 - (1 - gm) / (1 - d)).clip(lower=0)


def predicted_spend(m: pd.DataFrame, pb: dict) -> pd.Series:
    return m["pred_ltv"] if econ(pb)["return_basis"] == "lifetime" and "pred_ltv" in m.columns else m["pred_clv_365d"]


def _budget(strategy: dict | None) -> dict:
    return BUDGET_DEFAULTS | ((strategy or {}).get("budget") or {})


def roi(gp: float, cost: float) -> float | None:
    return (gp - cost) / cost if cost else None


def member_economics(m: pd.DataFrame, pb: dict) -> pd.DataFrame:
    """每位会员：预测毛利、一年内真实运营成本、用户 ROI，以及羊毛党是否发券。
    m 需有 stage / value_tier / action_type / incentive_group / pred_clv_365d，可选 discount_rate / pred_ltv。"""
    e = econ(pb)
    combos = {k: _budget(match_strategy(*k, pb)) for k in set(zip(m["stage"], m["value_tier"]))}
    b = pd.DataFrame([combos[k] for k in zip(m["stage"], m["value_tier"])], index=m.index)

    gp = predicted_spend(m, pb).fillna(0) * effective_margin(m, pb)
    price_cut = m["incentive_group"].isin(["price_sensitive", "deal_hunter"])
    incentive = np.where(price_cut, b["price_cut_value"], b["gift_value"] * e["gift_cost_ratio"]) * b["redemption_rate"]
    # 暂不打扰的会员本期没有动作，也不发权益
    incentive = np.where(m["action_type"] == "wait", 0.0, incentive)
    out = pd.DataFrame({
        "pred_gross_profit": gp,
        "fixed_cost": e["fixed_cost_per_member"],
        "action_cost": m["action_type"].map(e["action_cost"]).fillna(0) + b["touches"] * e["routine_touch_cost"],
        "incentive_cost": incentive,
    }, index=m.index)

    # 羊毛党逐人判断：自己的预测毛利抵不过 固定成本 + 动作投入 + 发券，就不发券
    deal = m["incentive_group"] == "deal_hunter"
    lose = deal & (gp < out[["fixed_cost", "action_cost", "incentive_cost"]].sum(axis=1)) & (out["incentive_cost"] > 0)
    out["coupon_decision"] = np.select([lose, deal], [NO_COUPON, "可发券（毛利覆盖成本）"], "")
    out.loc[lose, "incentive_cost"] = 0.0
    out["real_cost"] = out["fixed_cost"] + out["action_cost"] + out["incentive_cost"]
    out["member_roi"] = (out["pred_gross_profit"] - out["real_cost"]) / out["real_cost"].replace(0, np.nan)
    return out.round(4)


def budget_plan(m: pd.DataFrame, pb: dict) -> dict:
    """各 阶段×价值 人群的一年运营成本、预测毛利和 ROI；整体是否为正；羊毛党不发券省下多少。"""
    names, tiers = pb["stage_names"], pb["tier_names"]
    rows = []
    for (stage, tier), g in m.groupby(["stage", "value_tier"]):
        cost, gp = g["real_cost"].sum(), g["pred_gross_profit"].sum()
        r = roi(gp, cost)
        rows.append({
            "stage": stage, "value_tier": tier,
            "segment": f"{names.get(stage, stage)} · {tiers.get(tier, tier)}",
            "strategy": (match_strategy(stage, tier, pb) or {}).get("name", ""),
            "members": len(g), "real_cost": cost, "cost_per_member": cost / len(g),
            "fixed_cost": g["fixed_cost"].sum(), "action_cost": g["action_cost"].sum(), "incentive_cost": g["incentive_cost"].sum(),
            "pred_gross_profit": gp, "roi": r,
            "negative_roi_members": int((g["member_roi"] < 0).sum()),
            "deal_hunters": int((g["incentive_group"] == "deal_hunter").sum()),
            "deal_hunters_no_coupon": int((g["coupon_decision"] == NO_COUPON).sum()),
            "advice": "仅大促低成本触达（规则）" if stage == "lost"
            else "削减投入，只保留最低成本触达" if r is not None and r < 0
            else "压缩权益成本" if r is not None and r < 1 else "保持",
        })
    rows.sort(key=lambda x: -(x["roi"] if x["roi"] is not None else -9))
    total_cost, total_gp = m["real_cost"].sum(), m["pred_gross_profit"].sum()
    no_coupon = m[m["coupon_decision"] == NO_COUPON]
    saved = sum(_budget(match_strategy(s, t, pb))["price_cut_value"] * _budget(match_strategy(s, t, pb))["redemption_rate"]
                for s, t in zip(no_coupon["stage"], no_coupon["value_tier"]))
    neg = m["member_roi"] < 0
    return {"rows": rows, "total_cost": total_cost, "total_pred_gross_profit": total_gp, "overall_roi": roi(total_gp, total_cost),
            "deal_hunters": int((m["incentive_group"] == "deal_hunter").sum()),
            "deal_hunters_no_coupon": len(no_coupon), "coupon_cost_saved": saved,
            "negative_roi_members": int(neg.sum()), "negative_roi_cost": m.loc[neg, "real_cost"].sum(),
            "high_value_cost_share": m.loc[m["value_tier"] == "high", "real_cost"].sum() / total_cost if total_cost else None,
            "high_value_profit_share": m.loc[m["value_tier"] == "high", "pred_gross_profit"].sum() / total_gp if total_gp else None}


def simulate(g: pd.DataFrame, pb: dict, channel: str, cost_per_member: float, redemption_rate: float,
             touches: int, incentive_type: str) -> dict:
    """一次活动的用户 ROI：目标会员的预测毛利 vs 真实成本（固定成本 + 动作投入 + 让利全额 / 赠品成本价）。"""
    e = econ(pb)
    n = len(g)
    member_gp = predicted_spend(g, pb).fillna(0) * effective_margin(g, pb)
    gp = float(member_gp.sum())
    factor = e["gift_cost_ratio"] if incentive_type == "gift" else 1.0
    redeemers = n * redemption_rate
    incentive = redeemers * cost_per_member * factor
    fixed = n * e["fixed_cost_per_member"]
    touch = n * touches * e["action_cost"]["manual" if channel == "manual" else "auto"]
    cost = fixed + incentive + touch
    return {
        "target_members": n,
        "return_basis": f"预测未来 {e['lifetime_years']} 年消费（折现）" if e["return_basis"] == "lifetime" else "预测未来一年消费",
        "pred_spend": float(predicted_spend(g, pb).sum()),
        "pred_gross_profit": gp,
        "expected_redeemers": redeemers,
        "real_cost": cost,
        "cost_breakdown": {"fixed": fixed, "action": touch, "incentive": incentive},
        "roi": roi(gp, cost),
        # 每位核销者的权益（面额或赠品标价）最多给到多少，整体还不亏
        "break_even_value_per_redeemer": max(0.0, gp - touch - fixed) / (redeemers * factor) if redeemers else None,
        "members_not_covering_cost": int((member_gp < (cost / n if n else 0)).sum()),
    }

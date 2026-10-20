"""活动与对照实验：上线时随机留对照组，结束后用两组"实际消费 − 预测消费"的差距，算出活动真正带来的额外消费。

为什么不直接看"实际 vs 预测"：季节、大促、大盘都会让实际偏离预测，只有和同期不触达的对照组比，
多出来的部分才是活动的功劳。
- 分组：按风险金额分 5 层，每层内随机抽取对照组，保证两组可比
- 增量毛利 = 两组差距 × 实验组人数 × 实验组实际毛利率；增量成本 = 动作投入 + 权益（固定成本不论做不做都要花，不计入）
- 增量 ROI =（增量毛利 − 增量成本）÷ 增量成本
记录存在 data/campaigns.json（不进 git）。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

from . import economics
from .data import PROCESSED_DIR

CAMPAIGNS_PATH = PROCESSED_DIR.parent / "campaigns.json"
Z_ALPHA, Z_BETA = 1.96, 0.84   # 双侧 95% 置信、80% 检验力


def load_all() -> list[dict]:
    return json.loads(CAMPAIGNS_PATH.read_text(encoding="utf-8")) if CAMPAIGNS_PATH.exists() else []


def save_all(campaigns: list[dict]) -> None:
    CAMPAIGNS_PATH.write_text(json.dumps(campaigns, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def assign(strata: pd.Series, holdout_pct: float, seed: int) -> pd.Series:
    """每层内随机抽 holdout_pct 做对照组。"""
    r = pd.Series(np.random.default_rng(seed).random(len(strata)), index=strata.index)
    rank = r.groupby(strata).rank(pct=True)
    return pd.Series(np.where(rank <= holdout_pct, "control", "treat"), index=strata.index)


def detectable_lift(sd: float, mean: float, n_t: int, n_c: int) -> float | None:
    """以当前样本量，能可靠测出的最小消费提升比例。"""
    if not (n_t and n_c and mean):
        return None
    return (Z_ALPHA + Z_BETA) * sd * np.sqrt(1 / n_t + 1 / n_c) / mean


def required_per_group(sd: float, mean: float, lift: float = 0.05) -> int | None:
    """想测出 lift 的消费提升，每组至少需要多少人。"""
    if not mean:
        return None
    return int(np.ceil(2 * (Z_ALPHA + Z_BETA) ** 2 * sd ** 2 / (lift * mean) ** 2))


def baseline(g: pd.DataFrame, start: pd.Timestamp, snapshot: pd.Timestamp, checkpoint: pd.Timestamp | None,
             checkpoint_days: int, window_days: int) -> tuple[pd.DataFrame, str]:
    """每位会员在活动窗口内的预测消费（基线）。上线日是观察日之后用当前预测；
    回放上线日恰好是付费预期检查点时，用检查点上的预测。"""
    if start >= snapshot:
        k = window_days / 90
        return pd.DataFrame({"amount": g["pred_clv_90d"] * k, "purchases": g["pred_purchases_90d"] * k}), "live"
    if checkpoint is not None and start == checkpoint and window_days == checkpoint_days:
        b = pd.DataFrame({"amount": g["expected_amount_cp"], "purchases": g["expected_purchases_cp"]}).dropna()
        return b, "replay"
    raise ValueError(f"只能从观察日（{snapshot.date()}）开始上线，或在付费预期检查点（{checkpoint.date() if checkpoint is not None else '—'}）"
                     f"回放 {checkpoint_days} 天的演示活动")


def create(members: pd.DataFrame, pb: dict, snapshot: pd.Timestamp, checkpoint: pd.Timestamp | None,
           checkpoint_days: int, *, name: str, stage: str, value_tier: str, action: str,
           cost_per_member: float, incentive_type: str = "gift", redemption_rate: float = 0.3,
           incentive_group: str | None = None, channel: str = "auto", window_days: int = 90,
           holdout_pct: float = 0.1, start_date: str | None = None) -> dict:
    if not 0.02 <= holdout_pct <= 0.5:
        raise ValueError("对照组比例应在 2%~50% 之间")
    g = members[(members["stage"] == stage) & (members["value_tier"] == value_tier)]
    if incentive_group:
        g = g[g["incentive_group"] == incentive_group]
    start = pd.Timestamp(start_date) if start_date else snapshot
    base, mode = baseline(g, start, snapshot, checkpoint, checkpoint_days, window_days)
    # 已在其他进行中活动里的会员不重复入组，否则两个活动的效果会混在一起
    busy = {k for c in load_all() if c["mode"] == "live" and pd.Timestamp(c["end_date"]) > snapshot for k in c["members"]}
    excluded = int(base.index.isin(list(busy)).sum()) if mode == "live" else 0
    if excluded:
        base = base[~base.index.isin(list(busy))]
    g = g.loc[base.index]
    if len(g) < 20:
        raise ValueError(f"目标人群只有 {len(g)} 人，太少，无法分出可比的对照组")

    cid = "c" + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)
    strata = g["value_at_risk"].rank(method="first", pct=True).mul(5).clip(upper=4.999).astype(int)
    group = assign(strata, holdout_pct, seed=int(cid[1:15] + str(int(cid[15:], 16))) % 2 ** 32)
    margin = economics.effective_margin(g, pb)

    e = economics.econ(pb)
    factor = e["gift_cost_ratio"] if incentive_type == "gift" else 1.0
    unit_cost = e["action_cost"]["manual" if channel == "manual" else "auto"] + cost_per_member * redemption_rate * factor

    t, c = group == "treat", group == "control"
    sd, mean = float(base["amount"].std()), float(base["amount"].mean())
    record = {
        "id": cid, "name": name, "created_at": datetime.now().isoformat(timespec="seconds"), "mode": mode,
        "stage": stage, "value_tier": value_tier, "incentive_group": incentive_group, "action": action,
        "channel": channel, "incentive_type": incentive_type, "cost_per_member": cost_per_member,
        "redemption_rate": redemption_rate, "unit_cost": round(unit_cost, 2),
        "start_date": str(start.date()), "end_date": str((start + pd.Timedelta(days=window_days)).date()),
        "window_days": window_days, "holdout_pct": holdout_pct,
        "design": {
            "treat_members": int(t.sum()), "control_members": int(c.sum()), "excluded_in_other_campaigns": excluded,
            "balance": {k: {"treat": float(g.loc[t, col].mean()), "control": float(g.loc[c, col].mean())}
                        for k, col in (("历史累计消费", "monetary"), ("流失概率", "churn_prob"), ("风险金额", "value_at_risk"))},
            "baseline_per_member": mean,
            "detectable_lift": detectable_lift(sd, mean, int(t.sum()), int(c.sum())),
            "per_group_for_5pct": required_per_group(sd, mean, 0.05),
        },
        "members": {k: [group[k], round(float(base.at[k, "amount"]), 2), round(float(base.at[k, "purchases"]), 4),
                        round(float(margin[k]), 4)] for k in g.index},
        "result": None,
    }
    campaigns = load_all()
    campaigns.append(record)
    save_all(campaigns)
    return record


def evaluate(record: dict, orders: pd.DataFrame, snapshot: pd.Timestamp) -> dict:
    start, end = pd.Timestamp(record["start_date"]), pd.Timestamp(record["end_date"])
    covered = min(snapshot, end)
    days = (covered - start).days
    if days <= 0:
        return {"status": "尚无数据", "note": f"活动 {record['start_date']} 开始，数据截至 {snapshot.date()}，跑管线更新数据后再评估"}

    m = pd.DataFrame.from_dict(record["members"], orient="index", columns=["group", "base_amount", "base_purchases", "margin"])
    o = orders[(orders["order_time"] > start) & (orders["order_time"] <= covered) & orders["customer_id"].isin(m.index)]
    m["actual"] = o.groupby("customer_id")["amount"].sum().reindex(m.index).fillna(0)
    m["bought"] = m.index.isin(o["customer_id"])
    k = days / record["window_days"]
    m["gap"] = m["actual"] - m["base_amount"] * k

    t, c = m[m["group"] == "treat"], m[m["group"] == "control"]
    diff = t["gap"].mean() - c["gap"].mean()
    test = stats.ttest_ind(t["gap"], c["gap"], equal_var=False)
    se = np.sqrt(t["gap"].var() / len(t) + c["gap"].var() / len(c))
    inc_revenue = diff * len(t)
    inc_gp = inc_revenue * t["margin"].mean()
    cost = record["unit_cost"] * len(t)
    roi = economics.roi(inc_gp, cost)
    significant = bool(test.pvalue < 0.05)
    verdict = ("有效：额外毛利覆盖成本，可以继续或加码" if significant and roi is not None and roi > 0
               else "带来了额外消费，但不够覆盖成本：压缩权益成本" if significant and diff > 0
               else "没有测出可靠的额外消费（区间包含 0）：不要据此加码，可扩大对照组或延长周期再测")

    def side(df):
        return {"members": len(df), "actual_per_member": df["actual"].mean(), "baseline_per_member": df["base_amount"].mean() * k,
                "gap_per_member": df["gap"].mean(), "purchase_rate": df["bought"].mean()}

    result = {
        "status": "已结束" if snapshot >= end else f"进行中（已覆盖 {days}/{record['window_days']} 天）",
        "evaluated_at": datetime.now().isoformat(timespec="seconds"), "data_until": str(covered.date()),
        "treat": side(t), "control": side(c),
        "incremental_per_member": diff, "ci95": [diff - 1.96 * se, diff + 1.96 * se], "p_value": float(test.pvalue),
        "significant": significant,
        "measured_lift": diff / (t["base_amount"].mean() * k) if t["base_amount"].mean() else None,
        "incremental_revenue": inc_revenue, "incremental_gross_profit": inc_gp, "incremental_cost": cost,
        "incremental_roi": roi, "verdict": verdict,
    }
    if record["mode"] == "replay":
        result["note"] = ("回放演示：这个活动没有真实执行，两组都没被触达，组间差距应接近 0，用来检验评估方法不会凭空算出效果。"
                          "人群是按今天的阶段圈的，两组的'实际 − 预测'都偏低，但组间对比仍然公平。回放结果不计入预算表的实测证据")
    return result


def summary(record: dict) -> dict:
    """去掉逐人名单，给列表和大模型看。"""
    return {k: v for k, v in record.items() if k != "members"}


def latest_result(campaigns: list[dict], stage: str, value_tier: str) -> dict | None:
    """某人群最近一次真实上线并评估过的活动结果，供预算表显示实测增量 ROI。回放演示不算证据。"""
    done = [c for c in campaigns if c["stage"] == stage and c["value_tier"] == value_tier and c.get("result")
            and "incremental_roi" in c["result"] and c["mode"] == "live"]
    if not done:
        return None
    c = max(done, key=lambda x: x["result"]["evaluated_at"])
    r = c["result"]
    return {"campaign": c["name"], "mode": c["mode"], "incremental_roi": r["incremental_roi"],
            "measured_lift": r["measured_lift"], "significant": r["significant"], "verdict": r["verdict"]}


def holdout_members(campaigns: list[dict], snapshot: pd.Timestamp) -> dict[str, str]:
    """进行中活动的对照组会员 -> 活动名。对照组在活动期间不能被任何渠道触达。"""
    out = {}
    for c in campaigns:
        if c["mode"] == "live" and pd.Timestamp(c["end_date"]) > snapshot:
            out.update({k: c["name"] for k, v in c["members"].items() if v[0] == "control"})
    return out

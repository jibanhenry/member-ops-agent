"""用户画像：RFM 八类分群、生命周期阶段、价值分层、策略匹配。规则阈值全部来自 playbook.yaml。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbook"

RFM_NAMES = {
    (1, 1, 1): "重要价值客户",
    (0, 1, 1): "重要保持客户",
    (1, 0, 1): "重要发展客户",
    (0, 0, 1): "重要挽留客户",
    (1, 1, 0): "一般价值客户",
    (0, 1, 0): "一般保持客户",
    (1, 0, 0): "一般发展客户",
    (0, 0, 0): "一般挽留客户",
}


@lru_cache
def load_playbook() -> dict:
    return yaml.safe_load((PLAYBOOK_DIR / "playbook.yaml").read_text(encoding="utf-8"))


def rfm_segment(snap: pd.DataFrame) -> pd.Series:
    """经典 RFM 八类：R/F/M 各自与中位数比较，高=1 低=0。"""
    r = (snap["recency_days"] <= snap["recency_days"].median()).astype(int)
    f = (snap["frequency"] > snap["frequency"].median()).astype(int)
    m = (snap["monetary"] >= snap["monetary"].median()).astype(int)
    return pd.Series([RFM_NAMES[k] for k in zip(r, f, m)], index=snap.index)


def value_tier(snap: pd.DataFrame, pb: dict) -> pd.Series:
    pct = snap["monetary"].rank(ascending=False, pct=True)
    cfg = pb["value_tiers"]
    return pd.Series(
        np.select([pct <= cfg["high_top_pct"], pct <= cfg["mid_top_pct"]], ["high", "mid"], "low"),
        index=snap.index,
    )


def lifecycle_stage(df: pd.DataFrame, pb: dict) -> pd.Series:
    """df 需包含 recency_days / tenure_days / frequency / recency_gap_ratio / churn_prob / cutoff，可选 reg_date。"""
    c = pb["lifecycle"]
    churn_prob = df["churn_prob"].fillna(0)
    gap_ratio = df["recency_gap_ratio"].fillna(0)
    # 有入会时间就按入会算新客；数据窗口内的"首次消费"可能只是窗口开始前的老会员
    tenure = df["tenure_days"]
    if "reg_date" in df.columns:
        reg_days = (df["cutoff"] - pd.to_datetime(df["reg_date"])).dt.days
        tenure = reg_days.fillna(tenure)
    conditions = [
        df["recency_days"] > c["lost_recency_days"],
        df["recency_days"] > c["dormant_recency_days"],
        tenure <= c["new_tenure_days"],
        (churn_prob >= c["at_risk_churn_prob"]) | (gap_ratio >= c["at_risk_gap_ratio"]),
        df["frequency"] <= c["growing_max_orders"],
    ]
    choices = ["lost", "dormant", "new", "at_risk", "growing"]
    return pd.Series(np.select(conditions, choices, "mature"), index=df.index)


def match_strategy(stage: str, tier: str, pb: dict | None = None) -> dict | None:
    pb = pb or load_playbook()
    exact = [s for s in pb["strategies"] if s["stage"] == stage and s["tier"] == tier]
    fallback = [s for s in pb["strategies"] if s["stage"] == stage and s["tier"] == "any"]
    return (exact or fallback or [None])[0]

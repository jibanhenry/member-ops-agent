"""洞察：付费是否达到预期、高价值会员长什么样、哪些拉新来源带来高价值会员。

- 预期 vs 实际：在检查点（N 天前）用付费预测模型算出每人"到今天应该买几次"，和实际对比；
  明显没达到的标记"未达预期"，并给出可读的原因，作为下一步动作的触发条件
- 高价值画像：各维度在高价值会员里的占比 vs 整体占比（TGI），每次跑管线存一份，便于定期对比
- 拉新来源：按首单品牌、首单金额段看新会员一年后的消费和成为高价值的比例
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULTS = {"checkpoint_days": 90, "min_expected_purchases": 1.0, "shortfall_ratio": 0.5}
PORTRAIT_DIMS = {"age_band": "年龄段", "gender": "性别", "tenure_band": "入会年限", "top_brand": "主要消费品牌",
                 "first_brand": "首单品牌", "first_amount_band": "首单金额段", "age": "年龄段", "income": "收入段",
                 "household_comp": "家庭结构", "top_department": "主要消费品类"}
FIRST_AMOUNT_BINS = [0, 1000, 3000, 10000, np.inf]
FIRST_AMOUNT_LABELS = ["首单<1千", "首单1千-3千", "首单3千-1万", "首单≥1万"]


def cfg(pb: dict) -> dict:
    return DEFAULTS | (pb.get("expectation") or {})


# ---------- 预期 vs 实际 ----------

def shortfall(m: pd.DataFrame, pb: dict) -> pd.Series:
    c = cfg(pb)
    e, a = m["expected_purchases_cp"], m["actual_purchases_cp"]
    return (e >= c["min_expected_purchases"]) & (a < e * c["shortfall_ratio"])


def shortfall_reasons(orders: pd.DataFrame, m: pd.DataFrame, cutoff: pd.Timestamp, pb: dict) -> pd.Series:
    """对比检查点前后（各 N 天）的消费：沉默、客单下降、品牌收窄、只在打折时回来、间隔拉长。"""
    days = cfg(pb)["checkpoint_days"]
    cp = cutoff - pd.Timedelta(days=days)
    o = orders[orders["order_time"] > cp - pd.Timedelta(days=365)]
    after, before = o[o["order_time"] > cp], o[o["order_time"] <= cp]

    def stats(df):
        g = df.groupby("customer_id")
        s = pd.DataFrame({"n": g.size(), "aov": g["amount"].mean()})
        if "brand" in df.columns:
            s["brands"] = g["brand"].nunique()
        if "discount" in df.columns:
            s["promo"] = (df["discount"] > 0).groupby(df["customer_id"]).mean()
        return s

    a, b = stats(after).reindex(m.index), stats(before).reindex(m.index)
    parts = []

    def add(mask, text):
        text = text if isinstance(text, pd.Series) else pd.Series(text, index=m.index)
        parts.append(text.where(mask.fillna(False) & m["shortfall"]))

    money = lambda v: v.round(-1).map("{:,.0f}".format)
    add(a["n"].isna(), f"检查点后 {days} 天没有到柜")
    add(a["aov"] < b["aov"] * 0.7, "客单从 " + money(b["aov"]) + " 降到 " + money(a["aov"]))
    if "brands" in a.columns:
        add((b["brands"] >= 2) & (a["brands"] < b["brands"]),
            "购买品牌从 " + b["brands"].fillna(0).astype(int).astype(str) + " 个减到 "
            + a["brands"].fillna(0).astype(int).astype(str) + " 个，可能部分品类转去别处买")
    if "promo" in a.columns:
        add((a["promo"] == 1) & (b["promo"] < 0.5), "最近只在有折扣时才消费")
    if "recency_gap_ratio" in m.columns:
        add(m["recency_gap_ratio"] >= 1.5, "已沉默 " + m["recency_days"].astype(str) + " 天，超过平时间隔的 1.5 倍")
    reasons = pd.concat(parts, axis=1)
    return reasons.apply(lambda r: "；".join(r.dropna()), axis=1).where(m["shortfall"], "")


# ---------- 画像与拉新来源 ----------

def first_order_traits(orders: pd.DataFrame) -> pd.DataFrame:
    first = orders.sort_values("order_time").groupby("customer_id").first()
    out = pd.DataFrame({"first_order_amount": first["amount"]})
    out["first_amount_band"] = pd.cut(first["amount"], FIRST_AMOUNT_BINS, labels=FIRST_AMOUNT_LABELS, right=False).astype(str)
    if "brand" in first.columns:
        out["first_brand"] = first["brand"]
    return out


def value_portrait(m: pd.DataFrame, min_share: float = 0.03, top: int = 8) -> dict:
    """各维度在高价值会员中的占比 / 整体占比 × 100（TGI），列出高价值会员显著集中的特征。"""
    high = m[m["value_tier"] == "high"]
    dims = {k: v for k, v in PORTRAIT_DIMS.items() if k in m.columns and m[k].nunique() > 1}
    if "age_band" in dims:
        dims.pop("age", None)
    out = []
    for col, name in dims.items():
        all_share = m[col].value_counts(normalize=True)
        high_share = high[col].value_counts(normalize=True)
        df = pd.DataFrame({"high_share": high_share, "all_share": all_share}).dropna()
        df = df[df["high_share"] >= min_share]
        df["tgi"] = df["high_share"] / df["all_share"] * 100
        for v, r in df.iterrows():
            out.append({"dimension": name, "value": str(v), "high_share": r["high_share"], "all_share": r["all_share"], "tgi": r["tgi"]})
    out.sort(key=lambda x: -x["tgi"])
    stats = lambda g: {"members": len(g), "avg_spend": g["monetary"].mean(), "avg_orders": g["frequency"].mean(),
                       "avg_aov": g["aov"].mean(), "avg_n_brands": g["n_brands"].mean() if "n_brands" in g else None,
                       "avg_discount_rate": g["discount_rate"].mean() if "discount_rate" in g else None,
                       "avg_pred_ltv": g["pred_ltv"].mean() if "pred_ltv" in g else None}
    return {"over_index": out[:top], "under_index": sorted(out, key=lambda x: x["tgi"])[:top],
            "high": stats(high), "all": stats(m)}


def acquisition_quality(orders: pd.DataFrame, m: pd.DataFrame, cutoff: pd.Timestamp, min_members: int = 100) -> dict:
    """首单在 cutoff 一年以前的会员：按首单品牌、首单金额段看首年消费和现在成为高价值的比例。"""
    first = orders.groupby("customer_id")["order_time"].min()
    cohort = first[first <= cutoff - pd.Timedelta(days=365)].index
    o = orders[orders["customer_id"].isin(cohort)].merge(first.rename("first"), left_on="customer_id", right_index=True)
    year1 = o[o["order_time"] < o["first"] + pd.Timedelta(days=365)].groupby("customer_id")["amount"].sum()
    c = m.loc[m.index.intersection(cohort)].assign(year1=year1)

    def table(col):
        if col not in c.columns:
            return []
        g = c.groupby(col).agg(members=("year1", "size"), first_year_spend=("year1", "mean"),
                               high_share=("value_tier", lambda s: (s == "high").mean()),
                               deal_hunter_share=("incentive_group", lambda s: (s == "deal_hunter").mean()))
        g = g[g["members"] >= min_members].sort_values("first_year_spend", ascending=False)
        return g.reset_index().rename(columns={col: "group"}).to_dict("records")

    base = {"members": len(c), "first_year_spend": c["year1"].mean(), "high_share": (c["value_tier"] == "high").mean()}
    return {"cohort_note": f"首单在 {(cutoff - pd.Timedelta(days=365)).date()} 及以前的 {len(c):,} 位会员", "overall": base,
            "by_first_brand": table("first_brand"), "by_first_amount": table("first_amount_band")}


def save_history(path: Path, snapshot_date: str, portrait: dict) -> dict | None:
    """按观察日保存画像快照，返回上一期快照（没有则为 None），用于定期对比。"""
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    prev_dates = sorted(d for d in hist if d < snapshot_date)
    hist[snapshot_date] = portrait
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return {"snapshot_date": prev_dates[-1], **hist[prev_dates[-1]]} if prev_dates else None

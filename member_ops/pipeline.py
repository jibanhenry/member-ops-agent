"""端到端管线：订单 -> 特征 -> 付费预测 / 流失预警 -> 画像与生命周期 -> 落盘。

运行：uv run python -m member_ops.pipeline
产出：
    data/processed/members.parquet     每位会员一行：特征、预测、分群、阶段、推荐策略、流失原因、下一步动作
    data/processed/model_report.json   数据概况 + 模型回测指标（用于"责任可信"展示）
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import actions, churn, clv, economics, insights, segments
from .data import CONFIG, DATASET_INFO, PROCESSED_DIR, load_dataset
from .features import build_snapshot, future_labels

HORIZON_DAYS = 90                               # 付费预测时间窗
CHURN_HORIZON_DAYS = CONFIG.churn_horizon_days  # 流失定义：之后 N 天不再消费，按业态频率配置


def seasonal_factors(orders: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[float, float]:
    """BG/NBD 假设购买节奏平稳，礼品/零售有明显旺淡季（旺季不仅买得多，单笔也更大）。
    用"去年同期"校准：在 cutoff-365 天处拟合并预测之后 90 天，
    返回 (购买次数系数, 收入系数) = 实际 / 预测。历史不足 180 天时不校准。"""
    ly = cutoff - pd.Timedelta(days=365)
    if ly - orders["order_time"].min() < pd.Timedelta(days=180):
        return 1.0, 1.0
    snap = build_snapshot(orders, ly)
    pred, _ = clv.fit_and_score(snap, horizon_days=(HORIZON_DAYS,))
    actual = future_labels(orders, ly, HORIZON_DAYS).reindex(snap.index).fillna(0)
    return (
        float(actual["future_orders"].sum() / pred[f"pred_purchases_{HORIZON_DAYS}d"].sum()),
        float(actual["future_amount"].sum() / pred[f"pred_clv_{HORIZON_DAYS}d"].sum()),
    )


def member_expectation(orders: pd.DataFrame, cal: pd.Timestamp, days: int) -> tuple[pd.DataFrame, pd.DataFrame, float, float]:
    """在 cal 处拟合付费预测，得到每位会员之后 days 天的预期购买次数/金额（季节校准后）和实际发生。
    返回 (预期, 未校准预期, 次数系数, 收入系数)。"""
    snap = build_snapshot(orders, cal)
    pred, _ = clv.fit_and_score(snap, horizon_days=(days,))
    actual = future_labels(orders, cal, days).reindex(snap.index).fillna(0)
    f_buy, f_rev = seasonal_factors(orders, cal)
    raw = pd.DataFrame({"purchases": pred[f"pred_purchases_{days}d"], "amount": pred[f"pred_clv_{days}d"]})
    exp = pd.DataFrame({
        "expected_purchases": raw["purchases"] * f_buy, "expected_amount": raw["amount"] * f_rev,
        "actual_purchases": actual["future_orders"], "actual_amount": actual["future_amount"],
    })
    return exp, raw, f_buy, f_rev


def backtest_clv(orders: pd.DataFrame, end: pd.Timestamp) -> dict:
    """在 end-90 天处拟合，预测之后 90 天，与真实发生对比。"""
    cal = end - pd.Timedelta(days=HORIZON_DAYS)
    exp, raw, f_buy, f_rev = member_expectation(orders, cal, HORIZON_DAYS)
    snap_index = exp.index
    raw_buy, raw_clv = raw["purchases"], raw["amount"]
    p_buy, p_clv = exp["expected_purchases"], exp["expected_amount"]
    actual = pd.DataFrame({"future_orders": exp["actual_purchases"], "future_amount": exp["actual_amount"]})
    return {
        "calibration_end": str(cal.date()),
        "seasonal_factor_purchases": round(f_buy, 3),
        "seasonal_factor_revenue": round(f_rev, 3),
        "pred_total_purchases_unadjusted": round(float(raw_buy.sum()), 1),
        "pred_total_revenue_unadjusted": round(float(raw_clv.sum()), 0),
        "n_customers": int(len(snap_index)),
        "pred_total_purchases": round(float(p_buy.sum()), 1),
        "actual_total_purchases": int(actual["future_orders"].sum()),
        "purchases_mae": round(float((p_buy - actual["future_orders"]).abs().mean()), 3),
        "pred_total_revenue": round(float(p_clv.sum()), 0),
        "actual_total_revenue": round(float(actual["future_amount"].sum()), 0),
        "clv_spearman": round(float(spearmanr(p_clv, actual["future_amount"]).statistic), 4),
    }


def train_churn(orders: pd.DataFrame, end: pd.Timestamp):
    h = CHURN_HORIZON_DAYS
    t1 = end - pd.Timedelta(days=2 * h)
    t2 = end - pd.Timedelta(days=h)
    s1 = churn.in_play(build_snapshot(orders, t1))
    s2 = churn.in_play(build_snapshot(orders, t2))
    y1 = churn.churn_label(s1, future_labels(orders, t1, h))
    y2 = churn.churn_label(s2, future_labels(orders, t2, h))
    cols = churn.feature_cols(s1)

    model = churn.make_model().fit(s1[cols], y1)
    metrics = churn.evaluate(model, s2[cols], y2)
    metrics.update(train_cutoff=str(t1.date()), valid_cutoff=str(t2.date()), horizon_days=h)

    final = churn.make_model().fit(pd.concat([s1[cols], s2[cols]]), pd.concat([y1, y2]))
    return final, cols, metrics


def run() -> pd.DataFrame:
    t0 = time.time()
    orders, profiles, extra = load_dataset()
    end = orders["order_time"].max()
    print(f"[1/5] 订单 {len(orders):,} 笔，会员 {orders['customer_id'].nunique():,} 人，截至 {end.date()}")

    clv_bt = backtest_clv(orders, end)
    print(f"[2/5] 付费预测回测：购买 预测 {clv_bt['pred_total_purchases']:.0f} / 实际 {clv_bt['actual_total_purchases']} 次，"
          f"收入 预测 {clv_bt['pred_total_revenue']:,.0f} / 实际 {clv_bt['actual_total_revenue']:,.0f}，"
          f"CLV 排序相关 {clv_bt['clv_spearman']}")

    churn_model, churn_cols, churn_metrics = train_churn(orders, end)
    print(f"[3/5] 流失模型验证：AUC {churn_metrics['auc']}，Top10% 命中率 {churn_metrics['top10_precision']:.0%}"
          f"（基线流失率 {churn_metrics['churn_rate']:.0%}）")

    pb = segments.load_playbook()
    eco = economics.econ(pb)
    snap = build_snapshot(orders, end)
    pred, clv_params = clv.fit_and_score(
        snap, horizon_days=tuple(sorted({HORIZON_DAYS, *(365 * k for k in range(1, eco["lifetime_years"] + 1))})))
    pred["pred_ltv"] = economics.lifetime_value(pred, eco["lifetime_years"], eco["annual_discount_rate"])
    f_buy, f_rev = seasonal_factors(orders, end)
    pred[f"pred_purchases_{HORIZON_DAYS}d"] *= f_buy
    pred[f"pred_clv_{HORIZON_DAYS}d"] *= f_rev
    clv_params["seasonal_factors_90d"] = {"purchases": f_buy, "revenue": f_rev}
    members = snap.join(pred).join(profiles)
    play = churn.in_play(members)
    members["churn_prob"] = pd.Series(churn_model.predict_proba(play[churn_cols])[:, 1], index=play.index)
    print("[4/5] 当前快照打分完成")

    members["rfm_segment"] = segments.rfm_segment(members)
    members["value_tier"] = segments.value_tier(members, pb)
    members["value_shift"] = segments.value_shift(members, pb)
    members["stage"] = segments.lifecycle_stage(members, pb)
    members["stage_name"] = members["stage"].map(pb["stage_names"])
    members["tier_name"] = members["value_tier"].map(pb["tier_names"])
    members["strategy"] = [
        (segments.match_strategy(s, t, pb) or {}).get("name", "")
        for s, t in zip(members["stage"], members["value_tier"])
    ]
    members["churn_reasons"] = members.apply(lambda r: "；".join(churn.reason_codes(r)), axis=1)
    # 风险金额：如果这位会员流失，按近一年消费估算每年少收入多少
    members["amount_365d"] = (
        orders[orders["order_time"] > end - pd.Timedelta(days=365)].groupby("customer_id")["amount"].sum()
    ).reindex(members.index).fillna(0)
    members["value_at_risk"] = members["churn_prob"].fillna(0) * members["amount_365d"]

    # 行动编排：补货节奏、权益、成本上限、下一步动作
    rep = actions.cfg(pb, "replenishment")
    b_cycle, overall_cycle = actions.brand_cycles(orders, rep["brand_min_pairs"])
    members = members.join(actions.purchase_cycle(members, b_cycle, overall_cycle, end, pb))
    members["price_sensitive"] = actions.price_sensitive(members, pb)
    members["incentive_group"] = actions.incentive_group(members, pb)
    members = members.join(insights.first_order_traits(orders))

    # 付费预期跟踪：检查点上预测"到今天应该买几次"，和实际对比
    cp_days = insights.cfg(pb)["checkpoint_days"]
    exp, _, _, _ = member_expectation(orders, end - pd.Timedelta(days=cp_days), cp_days)
    members = members.join(exp.add_suffix("_cp"))
    members["shortfall"] = insights.shortfall(members, pb)
    members["shortfall_reasons"] = insights.shortfall_reasons(orders, members, end, pb)

    members = members.join(actions.plan_actions(members, end, pb))
    members = members.join(economics.member_economics(members, pb))

    members = members.reset_index()
    portrait = json.loads(json.dumps(insights.value_portrait(members), default=float))
    prev_portrait = insights.save_history(PROCESSED_DIR / "portrait_history.json", str(end.date()), portrait)
    members.to_parquet(PROCESSED_DIR / "members.parquet", index=False)

    report = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "dataset": DATASET_INFO | {
            "n_orders": int(len(orders)),
            "n_members": int(members.shape[0]),
            "date_range": [str(orders["order_time"].min().date()), str(end.date())],
            "snapshot_date": str(end.date()),
        },
        "clv_backtest": clv_bt,
        "clv_params": json.loads(json.dumps(clv_params, default=float)),
        "churn_validation": churn_metrics,
        "seasonality": actions.seasonality(orders),
        "expectation": {
            "checkpoint_date": str((end - pd.Timedelta(days=cp_days)).date()), "days": cp_days,
            "tracked_members": int(members["expected_purchases_cp"].notna().sum()),
            "expected_purchases": float(members["expected_purchases_cp"].sum()),
            "actual_purchases": float(members["actual_purchases_cp"].sum()),
            "shortfall_members": int(members["shortfall"].sum()),
        },
        "portrait": portrait,
        "portrait_previous": prev_portrait,
        "acquisition": json.loads(json.dumps(insights.acquisition_quality(orders, members.set_index("customer_id"), end),
                                             default=float)),
        "replenish_cycle": {"overall_median_days": overall_cycle,
                            "brands": {k: float(v) for k, v in b_cycle.sort_values().items()}},
        **extra,
        "churn_definition": f"在观察时点前 {churn.ACTIVE_WINDOW_DAYS} 天内有消费、且之后 {CHURN_HORIZON_DAYS} 天内未再消费",
    }
    (PROCESSED_DIR / "model_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=lambda o: float(o) if isinstance(o, np.floating) else str(o)),
        encoding="utf-8",
    )
    print(f"[5/5] 已写出 members.parquet / model_report.json（耗时 {time.time() - t0:.0f}s）")
    print(members["stage_name"].value_counts().to_string())
    print(members["action_type"].map(actions.ACTION_NAMES).value_counts().to_string())
    return members


if __name__ == "__main__":
    run()

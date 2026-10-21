"""Agent 工具层：把管线产出的会员数据包装成可查询的函数。

每个工具返回可 JSON 序列化的 dict，既供 LLM 调用，也供 Web 接口和离线规则引擎复用。
"""

from __future__ import annotations

import json
import math
from functools import cached_property

import pandas as pd
import pyarrow.parquet as pq

from . import actions, alipay, campaigns, economics, tasks
from .data import CONFIG, PROCESSED_DIR
from .segments import load_playbook, match_strategy

MEMBERS_PATH = PROCESSED_DIR / "members.parquet"

# 不同数据集的会员档案字段不同，只暴露当前数据里存在的维度
_ALL_DIMENSIONS = {
    "stage": "生命周期阶段",
    "value_tier": "价值分层",
    "action_type": "下一步动作",
    "incentive_group": "权益分组（高价值不降价/羊毛党/价格敏感/非价格敏感）",
    "first_brand": "首单品牌",
    "first_amount_band": "首单金额段",
    "rfm_segment": "RFM 分群",
    "top_brand": "主要消费品牌",
    "age_band": "年龄段",
    "gender": "性别",
    "tenure_band": "入会年限",
    "top_department": "主要消费品类",
    "age": "年龄段",
    "income": "收入段",
    "household_comp": "家庭结构",
}
_COLUMNS = set(pq.read_schema(MEMBERS_PATH).names) if MEMBERS_PATH.exists() else set(_ALL_DIMENSIONS)
DIMENSIONS = {k: v for k, v in _ALL_DIMENSIONS.items() if k in _COLUMNS}
if "age_band" in DIMENSIONS:  # 有年龄段时不按原始年龄拆分
    DIMENSIONS.pop("age", None)

# 会员画像弹窗/工具返回的字段：(列名, 中文名)，数据里没有的列自动跳过
PROFILE_SECTIONS = {
    "画像": [
        ("stage_name", "生命周期"), ("tier_name", "价值分层"), ("value_shift", "价值变化"), ("rfm_segment", "RFM 分群"),
        ("gender", "性别"), ("age", "年龄"), ("reg_date", "入会时间"), ("member_years", "入会年限"),
        ("top_brand", "主要消费品牌"), ("n_brands", "购买过的品牌数"),
        ("first_brand", "首单品牌"), ("first_order_amount", "首单金额"),
        ("top_department", "主要消费品类"), ("private_label_share", "自有品牌占比"), ("n_stores", "常去门店数"),
        ("income", "收入"), ("household_comp", "家庭结构"),
    ],
    "消费行为": [
        ("first_order", "首次消费"), ("last_order", "最近消费"), ("recency_days", "距今天数"),
        ("frequency", "累计消费次数"), ("monetary", "累计消费金额"), ("aov", "客单价"),
        ("mean_gap_days", "平均消费间隔天数"), ("spend_trend", "消费趋势(对数差)"),
    ],
    "优惠与积分": [
        ("discount_rate", "优惠占原价比例"), ("coupon_order_share", "用券订单占比"),
        ("points_total", "累计积分"), ("campaigns_received", "收到营销活动数"), ("coupons_redeemed", "核销优惠券数"),
        ("promo_order_share", "打折订单占比"), ("price_sensitive", "价格敏感"), ("incentive_group_name", "权益分组"),
    ],
    "付费预期": [
        ("expected_purchases_cp", "检查点以来预期消费次数"), ("actual_purchases_cp", "检查点以来实际消费次数"),
        ("expected_amount_cp", "检查点以来预期消费金额"), ("actual_amount_cp", "检查点以来实际消费金额"),
        ("shortfall", "未达预期"), ("shortfall_reasons", "未达预期原因"),
    ],
    "运营编排": [
        ("next_action", "下一步动作"), ("next_action_date", "动作日期"), ("channel_name", "触达方式"),
        ("cost_cap", "单人成本上限"), ("cycle_days", "补货周期天数"), ("cycle_source", "周期依据"),
        ("replenish_date", "补货提醒日"), ("replenish_status", "补货状态"),
        ("pred_gross_profit", "预测毛利"), ("fixed_cost", "固定成本"), ("action_cost", "动作投入"),
        ("incentive_cost", "权益成本"), ("real_cost", "一年运营成本"), ("member_roi", "用户 ROI"),
        ("coupon_decision", "羊毛党发券决定"),
    ],
}
CHANNEL_NAMES = {"manual": "导购人工", "auto": "自动触达", "promo_only": "只在大促触达"}
SORT_FIELDS = {"value_at_risk", "churn_prob", "pred_clv_365d", "pred_ltv", "monetary", "recency_days", "frequency"}


def _clean(obj):
    """NaN/numpy -> JSON 友好类型。"""
    if obj is pd.NaT:
        return None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, 4)
    if hasattr(obj, "item"):
        return _clean(obj.item())
    if isinstance(obj, pd.Timestamp):
        return str(obj.date())
    return obj


class MemberStore:
    def __init__(self):
        self.members = pd.read_parquet(PROCESSED_DIR / "members.parquet").set_index("customer_id")
        self.report = json.loads((PROCESSED_DIR / "model_report.json").read_text(encoding="utf-8"))
        self.playbook = load_playbook()
        if "channel" in self.members.columns:
            self.members["channel_name"] = self.members["channel"].map(CHANNEL_NAMES)
            self.members["action_name"] = self.members["action_type"].map(actions.ACTION_NAMES)
        if "incentive_group" in self.members.columns:
            self.members["incentive_group_name"] = self.members["incentive_group"].map(actions.INCENTIVE_GROUPS)
        self._planned = self.members[["action_type", "next_action", "next_action_date"]].copy() \
            if "action_type" in self.members.columns else None
        self._apply_holdouts()

    def _apply_holdouts(self) -> None:
        """进行中活动的对照组会员，活动期间不触达：覆盖其下一步动作。"""
        if self._planned is None:
            return
        m = self.members
        m[["action_type", "next_action", "next_action_date"]] = self._planned
        held = campaigns.holdout_members(campaigns.load_all(), self.snapshot)
        idx = m.index.intersection(list(held))
        if len(idx):
            m.loc[idx, "action_type"] = "holdout"
            m.loc[idx, "next_action"] = [f"对照组：活动「{held[k]}」期间不触达" for k in idx]
            m.loc[idx, "next_action_date"] = pd.NaT
        m["action_name"] = m["action_type"].map(actions.ACTION_NAMES)

    @cached_property
    def snapshot(self) -> pd.Timestamp:
        return pd.Timestamp(self.report["dataset"]["snapshot_date"])

    @cached_property
    def orders(self) -> pd.DataFrame:
        return pd.read_parquet(PROCESSED_DIR / f"{CONFIG.key}_orders.parquet", columns=["customer_id", "order_time", "amount"])

    @cached_property
    def currency(self) -> str:
        return self.report["dataset"]["currency"]

    @cached_property
    def churn_horizon(self) -> int:
        return self.report["churn_validation"]["horizon_days"]

    # ---------- 工具 ----------

    def get_overview(self) -> dict:
        m = self.members
        stage = (
            m.groupby("stage_name")
            .agg(members=("frequency", "size"), revenue=("monetary", "sum"), value_at_risk=("value_at_risk", "sum"))
            .sort_values("members", ascending=False)
        )
        stage["member_share"] = stage["members"] / len(m)
        stage["revenue_share"] = stage["revenue"] / m["monetary"].sum()
        return _clean({
            "snapshot_date": self.report["dataset"]["snapshot_date"],
            "currency": self.currency,
            "total_members": len(m),
            "active_members_30d": int((m["recency_days"] <= 30).sum()),
            "total_revenue": m["monetary"].sum(),
            "avg_order_value": m["aov"].mean(),
            "avg_orders_per_member": m["frequency"].mean(),
            "pred_revenue_next_90d": m["pred_clv_90d"].sum(),
            "pred_revenue_next_365d": m["pred_clv_365d"].sum(),
            "pred_lifetime_value": m["pred_ltv"].sum() if "pred_ltv" in m else None,
            "lifetime_years": economics.econ(self.playbook)["lifetime_years"],
            "shortfall_members": int(m["shortfall"].sum()) if "shortfall" in m else None,
            "total_value_at_risk": m["value_at_risk"].sum(),
            "high_risk_members": int((m["churn_prob"] >= self.playbook["lifecycle"]["at_risk_churn_prob"]).sum()),
            "high_risk_threshold": self.playbook["lifecycle"]["at_risk_churn_prob"],
            "churn_horizon_days": self.churn_horizon,
            "member_vs_nonmember": self.report.get("member_value"),
            "by_stage": stage.reset_index().to_dict("records"),
        })

    def segment_breakdown(self, dimension: str) -> dict:
        if dimension not in DIMENSIONS:
            raise ValueError(f"dimension 必须是 {list(DIMENSIONS)} 之一")
        m = self.members
        if dimension == "action_type":
            m = m.assign(action_type=m["action_name"])
        if dimension == "incentive_group":
            m = m.assign(incentive_group=m["incentive_group_name"])
        g = m.groupby(dimension, dropna=True)
        metrics = {
            "members": g.size(),
            "revenue": g["monetary"].sum(),
            "avg_spend_per_member": g["monetary"].mean(),
            "avg_order_value": g["aov"].mean(),
            "avg_orders": g["frequency"].mean(),
            "avg_churn_prob": g["churn_prob"].mean(),
            "value_at_risk": g["value_at_risk"].sum(),
            "pred_revenue_next_90d": g["pred_clv_90d"].sum(),
        }
        for col, name in (("discount_rate", "avg_discount_rate"), ("coupons_redeemed", "coupon_redeem_per_member"),
                          ("points_total", "avg_points")):
            if col in m.columns:
                metrics[name] = g[col].mean()
        df = pd.DataFrame(metrics).sort_values("members", ascending=False)
        if len(df) > 12:  # 品牌等高基数维度只看前 12 组，返回太长本地小模型读得很慢
            df = df.head(12)
        df["member_share"] = df["members"] / len(m)
        money_cols = ["revenue", "avg_spend_per_member", "avg_order_value", "value_at_risk", "pred_revenue_next_90d", "avg_points"]
        df[[c for c in money_cols if c in df]] = df[[c for c in money_cols if c in df]].round(0)
        df = df.round(3)
        coverage = m[dimension].notna().mean()
        note = f"该维度只有 {coverage:.0%} 的会员有数据" if coverage < 0.95 else None
        return _clean({"dimension": DIMENSIONS[dimension], "currency": self.currency, "note": note,
                       "groups": df.reset_index().rename(columns={dimension: "group"}).to_dict("records")})

    def filter_members(self, stage: str | None = None, value_tier: str | None = None, rfm_segment: str | None = None,
                       action_type: str | None = None, incentive_group: str | None = None,
                       shortfall: bool | None = None) -> pd.DataFrame:
        m = self.members
        if incentive_group:
            if incentive_group not in actions.INCENTIVE_GROUPS:
                raise ValueError(f"incentive_group 必须是 {list(actions.INCENTIVE_GROUPS)} 之一")
            m = m[m["incentive_group"] == incentive_group]
        if shortfall is not None:
            m = m[m["shortfall"] == shortfall]
        if action_type:
            if action_type not in actions.ACTION_NAMES:
                raise ValueError(f"action_type 必须是 {list(actions.ACTION_NAMES)} 之一")
            m = m[m["action_type"] == action_type]
        if stage:
            m = m[(m["stage"] == stage) | (m["stage_name"] == stage)]
        if value_tier:
            m = m[(m["value_tier"] == value_tier) | (m["tier_name"] == value_tier)]
        if rfm_segment:
            m = m[m["rfm_segment"] == rfm_segment]
        return m

    def find_members(self, stage: str | None = None, value_tier: str | None = None,
                     rfm_segment: str | None = None, min_churn_prob: float | None = None,
                     action_type: str | None = None, incentive_group: str | None = None, shortfall: bool | None = None,
                     sort_by: str = "value_at_risk", limit: int = 10, offset: int = 0) -> dict:
        m = self.filter_members(stage, value_tier, rfm_segment, action_type, incentive_group, shortfall)
        hint = None
        if min_churn_prob is not None:
            before = m
            m = m[m["churn_prob"] >= min_churn_prob]
            if m.empty and not before.empty:
                hint = (f"没有流失概率 ≥ {min_churn_prob:.0%} 的会员；这类人群的最高流失概率是 "
                        f"{before['churn_prob'].max():.1%}。去掉 min_churn_prob、按 value_at_risk 排序即可看到风险最高的会员，不要反复降低阈值。")
        if sort_by not in SORT_FIELDS:
            raise ValueError(f"sort_by 必须是 {sorted(SORT_FIELDS)} 之一")
        limit = max(1, min(int(limit), 50))
        cols = ["stage_name", "tier_name", "value_shift", "rfm_segment", "recency_days", "frequency", "monetary",
                "churn_prob", "pred_clv_365d", "value_at_risk", "strategy", "churn_reasons",
                "next_action", "next_action_date", "cost_cap", "pred_ltv", "incentive_group_name", "shortfall_reasons"]
        cols = [c for c in cols if c in m.columns]
        offset = max(0, int(offset))
        top = m.sort_values(sort_by, ascending=False).iloc[offset:offset + limit]
        return _clean({
            "hint": hint,
            "matched": len(m),
            "offset": offset,
            "matched_value_at_risk": m["value_at_risk"].sum(),
            "sorted_by": sort_by,
            "members": top[cols].reset_index().to_dict("records"),
        })

    def get_member_profile(self, customer_id: str) -> dict:
        cid = str(customer_id)
        if cid not in self.members.index:
            raise ValueError(f"找不到会员 {cid}")
        r = self.members.loc[cid]
        strategy = match_strategy(r["stage"], r["value_tier"], self.playbook)
        out = {"customer_id": cid}
        for section, fields in PROFILE_SECTIONS.items():
            out[section] = {label: r[col] for col, label in fields if col in r.index}
        out["预测"] = {
            "仍活跃概率": r["p_alive"], f"{self.churn_horizon}天流失概率": r["churn_prob"],
            "预测未来90天消费": r["pred_clv_90d"], "预测未来365天消费": r["pred_clv_365d"],
            "流失风险金额": r["value_at_risk"],
            f"预测未来{economics.econ(self.playbook)['lifetime_years']}年消费金额（折现）": r.get("pred_ltv"),
        }
        out["流失信号"] = [x for x in (r["churn_reasons"] or "").split("；") if x]
        if strategy and "incentive_group" in r.index:
            strategy = strategy | {"applied_incentive": actions.pick_incentive(strategy, r["incentive_group"])}
        out["推荐策略"] = strategy
        out["currency"] = self.currency
        return _clean(out)

    def get_strategy(self, stage: str, value_tier: str) -> dict:
        stage = self._code("stage", stage)
        value_tier = self._code("value_tier", value_tier)
        strategy = match_strategy(stage, value_tier, self.playbook)
        group = self.members[(self.members["stage"] == stage) & (self.members["value_tier"] == value_tier)]
        return _clean({
            "stage": self.playbook["stage_names"].get(stage, stage),
            "value_tier": self.playbook["tier_names"].get(value_tier, value_tier),
            "target_members": len(group),
            "group_value_at_risk": group["value_at_risk"].sum(),
            "group_avg_churn_prob": group["churn_prob"].mean(),
            "group_avg_discount_rate": group["discount_rate"].mean() if "discount_rate" in group else None,
            "price_sensitive_members": int(group["price_sensitive"].sum()) if "price_sensitive" in group else None,
            "avg_cost_cap_per_member": group["cost_cap"].mean() if "cost_cap" in group else None,
            "total_budget_cap": group["cost_cap"].sum() if "cost_cap" in group else None,
            "next_actions": group["action_name"].value_counts().to_dict() if "action_name" in group else None,
            "incentive_groups": [
                {"group": name, "members": int((group["incentive_group"] == k).sum()),
                 "incentive": actions.pick_incentive(strategy, k)}
                for k, name in actions.INCENTIVE_GROUPS.items() if (group["incentive_group"] == k).any()
            ] if "incentive_group" in group else None,
            "economics": {
                "real_cost": group["real_cost"].sum(), "pred_gross_profit": group["pred_gross_profit"].sum(),
                "roi": economics.roi(group["pred_gross_profit"].sum(), group["real_cost"].sum()),
                "negative_roi_members": int((group["member_roi"] < 0).sum()),
                "deal_hunters_no_coupon": int((group["coupon_decision"] == economics.NO_COUPON).sum()),
                "budget_assumptions": economics._budget(strategy),
            } if "real_cost" in group else None,
            "strategy": strategy,
            "说明": "单人成本上限 = 预测未来一年消费 × cost_cap_pct；高价值会员一律不降价，羊毛党发券回不了本就不发；"
                    "用户 ROI =（预测消费 × 实际毛利率 − 一年真实运营成本）÷ 真实运营成本；核销率和权益金额是 Playbook 里的假设",
        })

    def simulate_campaign(self, stage: str, value_tier: str, cost_per_member: float,
                          redemption_rate: float = 1.0, touches: int = 1, incentive_type: str = "gift",
                          incentive_group: str | None = None) -> dict:
        """活动的用户 ROI：目标会员的预测毛利 vs 真实成本（让利全额 / 赠品成本价 + 触达 + 导购人工）。"""
        stage = self._code("stage", stage)
        value_tier = self._code("value_tier", value_tier)
        if incentive_type not in ("gift", "price_cut", "experience"):
            raise ValueError("incentive_type 必须是 gift（赠品/小样，按成本价计）/ price_cut（满减折扣，按面额计）/ experience（体验服务，按成本计）")
        g = self.members[(self.members["stage"] == stage) & (self.members["value_tier"] == value_tier)]
        if incentive_group:
            g = g[g["incentive_group"] == incentive_group]
        strategy = match_strategy(stage, value_tier, self.playbook) or {}
        out = economics.simulate(g, self.playbook, strategy.get("channel", "auto"), cost_per_member,
                                 redemption_rate, touches, incentive_type)
        warnings = []
        no_cut = actions.cfg(self.playbook, "incentive_rules")["no_price_cut_tiers"]
        if incentive_type == "price_cut" and value_tier in no_cut:
            warnings.append("高价值会员不做价格减免：会伤害利润和身份感，请改用体验、身份、优先权类权益")
        if incentive_type == "price_cut" and not incentive_group:
            deal = int((g["incentive_group"] == "deal_hunter").sum())
            if deal:
                warnings.append(f"人群里有 {deal} 位羊毛党，建议用 incentive_group=deal_hunter 单独测算，毛利抵不过成本的不发券")
        cap = g["cost_cap"].mean() if len(g) else None
        if cap is not None and cost_per_member > cap:
            warnings.append(f"单人权益 {cost_per_member:,.0f} 超过该人群人均成本上限 {cap:,.0f}（预测未来一年消费 × Playbook 规定比例）")
        if out["roi"] is not None and out["roi"] < 0:
            warnings.append("ROI 为负：这批会员的预测毛利抵不过运营成本，建议不做或改用低成本触达，把预算留给 ROI 更高的人群")
        measured = campaigns.latest_result(campaigns.load_all(), stage, value_tier)
        base = g["pred_clv_90d"]
        incremental = measured or {
            "status": "待验证",
            "说明": "上面的 ROI 只说明这批会员值不值得花这些钱；钱有没有带来额外消费，需要上线时留对照组实测",
            "per_group_for_5pct_lift": campaigns.required_per_group(float(base.std()), float(base.mean()), 0.05),
            "target_members": len(g),
            "detectable_lift_with_10pct_holdout": campaigns.detectable_lift(
                float(base.std()), float(base.mean()), int(len(g) * 0.9), max(1, int(len(g) * 0.1))),
        }
        e = economics.econ(self.playbook)
        return _clean(out | {
            "incremental": incremental,
            "avg_cost_cap_per_member": cap,
            "warnings": warnings,
            "assumptions": {
                "cost_per_member": cost_per_member, "incentive_type": incentive_type, "redemption_rate": redemption_rate,
                "touches": touches, "gross_margin": e["gross_margin"], "gift_cost_ratio": e["gift_cost_ratio"],
                "fixed_cost_per_member": e["fixed_cost_per_member"], "action_cost": e["action_cost"],
                "说明": "回报 = 预测消费 × 会员实际毛利率（按其折扣率折算）；活动有没有带来额外消费，用实际消费 vs 预测消费的差距来检验",
            },
            "currency": self.currency,
        })

    def get_budget_plan(self) -> dict:
        """各人群本期投入、增量毛利、ROI 和建议；整体 ROI 是否为正；羊毛党不发券的人数。"""
        plan = economics.budget_plan(self.members, self.playbook)
        all_c = campaigns.load_all()
        for r in plan["rows"]:
            r["measured"] = campaigns.latest_result(all_c, r["stage"], r["value_tier"])
        e = economics.econ(self.playbook)
        return _clean(plan | {"currency": self.currency,
                              "assumptions": {"gross_margin": e["gross_margin"], "gift_cost_ratio": e["gift_cost_ratio"],
                                              "fixed_cost_per_member": e["fixed_cost_per_member"],
                                              "action_cost": e["action_cost"], "return_basis": e["return_basis"]},
                              "说明": "用户 ROI =（预测消费 × 实际毛利率 − 一年真实运营成本）÷ 真实运营成本；"
                                      "真实成本 = 固定成本 + 按下一步动作计价的动作投入 + 例行触达 + 权益（让利全额、赠品成本价），"
                                      "核销率和权益金额取自各策略的 budget 假设。用户 ROI 只说明这个人值不值得花这些钱；"
                                      "钱有没有带来额外消费看 measured（对照实验实测的增量 ROI），没有实验的人群是'待验证'"})

    def get_value_portrait(self) -> dict:
        """高价值会员画像（TGI）、与上一期的对比、拉新来源质量、高潜新会员。"""
        m = self.members
        potential = m[m["stage"].isin(["new", "one_time"]) & ((m["value_tier"] == "high") | (m["value_shift"] == "潜力上升"))]
        return _clean({
            "portrait": self.report.get("portrait"),
            "previous_snapshot_date": (self.report.get("portrait_previous") or {}).get("snapshot_date"),
            "acquisition": self.report.get("acquisition"),
            "high_potential_new_members": {"members": len(potential), "pred_ltv": potential["pred_ltv"].sum()},
            "currency": self.currency,
            "说明": "TGI = 该特征在高价值会员中的占比 ÷ 在全体会员中的占比 × 100，大于 120 说明高价值会员明显集中。"
                    "拉新来源按首单在一年以前的会员统计首年消费和现在成为高价值的比例",
        })

    def get_action_plan(self) -> dict:
        """本周行动计划：各类下一步动作的人数、人工回访产能与积压、补货窗口、近期大促档期。"""
        m = self.members
        cp = actions.cfg(self.playbook, "contact_policy")
        cutoff = pd.Timestamp(self.report["dataset"]["snapshot_date"])
        g = m.groupby("action_type").agg(members=("frequency", "size"), value_at_risk=("value_at_risk", "sum"),
                                         pred_revenue_next_90d=("pred_clv_90d", "sum"))
        acts = [{"action_type": k, "name": actions.ACTION_NAMES[k], **g.loc[k].to_dict(), "members": int(g.loc[k, "members"])}
                for k in actions.ACTION_NAMES if k in g.index]
        demand = int((m["channel"] == "manual").sum())
        cap = cp["manual_capacity_per_week"]
        scheduled = int(m["action_type"].isin(["manual", "manual_queue"]).sum())
        due = m[m["replenish_status"] == "补货窗口"]
        return _clean({
            "snapshot_date": str(cutoff.date()),
            "currency": self.currency,
            "actions": acts,
            "manual": {
                "demand": demand, "capacity_per_week": cap, "max_weeks": cp["manual_max_weeks"],
                "weeks_to_clear": -(-demand // cap) if cap else None,
                "scheduled": scheduled, "overflow_to_auto": demand - scheduled,
                "说明": "需要导购人工跟进的会员按风险金额排队；排不进的转自动触达。产能在 Playbook contact_policy 中配置",
            },
            "replenishment": {
                "in_window": len(due), "in_window_pred_revenue_90d": due["pred_clv_90d"].sum(),
                "cycle_source": m["cycle_source"].value_counts().to_dict(),
                "overall_median_cycle_days": self.report.get("replenish_cycle", {}).get("overall_median_days"),
            },
            "upcoming_events": [e | {"start": str(e["start"].date()), "end": str(e["end"].date())}
                                for e in actions.upcoming_events(cutoff, self.playbook, 180)],
            "contact_policy": cp,
            "price_sensitive_share": m["price_sensitive"].mean(),
            "seasonality": self.report.get("seasonality"),
        })

    def create_campaign(self, name: str, stage: str, value_tier: str, action: str, cost_per_member: float,
                        incentive_type: str = "gift", redemption_rate: float = 0.3, incentive_group: str | None = None,
                        window_days: int = 90, holdout_pct: float = 0.1, start_date: str | None = None) -> dict:
        """登记一个活动：按风险金额分层随机留对照组，记录每人的预测消费作为基线。"""
        stage = self._code("stage", stage)
        value_tier = self._code("value_tier", value_tier)
        exp = self.report.get("expectation") or {}
        checkpoint = pd.Timestamp(exp["checkpoint_date"]) if exp.get("checkpoint_date") else None
        channel = (match_strategy(stage, value_tier, self.playbook) or {}).get("channel", "auto")
        rec = campaigns.create(self.members, self.playbook, self.snapshot, checkpoint, exp.get("days", 90),
                               name=name, stage=stage, value_tier=value_tier, action=action,
                               cost_per_member=cost_per_member, incentive_type=incentive_type,
                               redemption_rate=redemption_rate, incentive_group=incentive_group, channel=channel,
                               window_days=window_days, holdout_pct=holdout_pct, start_date=start_date)
        self._apply_holdouts()
        return _clean(campaigns.summary(rec))

    def list_campaigns(self) -> dict:
        names, tiers = self.playbook["stage_names"], self.playbook["tier_names"]
        return _clean({"campaigns": [campaigns.summary(c) | {"stage_name": names.get(c["stage"]), "tier_name": tiers.get(c["value_tier"])}
                                     for c in campaigns.load_all()], "currency": self.currency})

    def evaluate_campaign(self, campaign_id: str) -> dict:
        """用实验组和对照组"实际 − 预测"的差距，算活动带来的额外消费和增量 ROI，并保存结果。"""
        all_c = campaigns.load_all()
        rec = next((c for c in all_c if c["id"] == campaign_id), None)
        if rec is None:
            raise ValueError(f"找不到活动 {campaign_id}")
        result = campaigns.evaluate(rec, self.orders, self.snapshot)
        if "incremental_roi" in result:
            rec["result"] = result
            campaigns.save_all(all_c)
        return _clean(campaigns.summary(rec) | {"result": result, "currency": self.currency})

    def delete_campaign(self, campaign_id: str) -> dict:
        all_c = campaigns.load_all()
        rest = [c for c in all_c if c["id"] != campaign_id]
        if len(rest) == len(all_c):
            raise ValueError(f"找不到活动 {campaign_id}")
        campaigns.save_all(rest)
        self._apply_holdouts()
        return {"deleted": campaign_id}

    # ---------- 智能体工作台 ----------

    def get_weekly_brief(self) -> dict:
        """智能体每周主动给店长的简报：本周要做的事、钱花得怎样、需要决定的事。"""
        f = tasks.weekly_facts(self)
        return _clean({"facts": f, "brief": tasks.template_brief(f, self.currency)})

    def list_guide_tasks(self, counter: str | None = None, status: str | None = None, limit: int = 20) -> dict:
        t = tasks.task_frame(self.members)
        summary = {"total": len(t), "counters": t["counter"].value_counts().head(30).to_dict(),
                   "status_counts": t["status"].value_counts().to_dict()}
        if counter:
            t = t[t["counter"] == counter]
        if status:
            t = t[t["status"] == status]
        names, tiers = self.playbook["stage_names"], self.playbook["tier_names"]
        rows = [{"customer_id": k, "counter": r["counter"], "task_type": r["task_type"], "stage_name": names.get(r["stage"]),
                 "tier_name": tiers.get(r["value_tier"]), "recency_days": r["recency_days"], "value_at_risk": r["value_at_risk"],
                 "reason": ((r["shortfall_reasons"] if r["action_type"] == "shortfall" else r["churn_reasons"]) or "").split("；")[0],
                 "incentive_group_name": r["incentive_group_name"], "status": r["status"], "result": r["result"]}
                for k, r in t.head(max(1, min(int(limit), 100))).iterrows()]
        return _clean(summary | {"matched": len(t), "tasks": rows})

    def get_draft_context(self, customer_id: str, channel: str = "wecom") -> dict:
        """起草话术需要的事实、模板话术和合规规则；话术由导购确认后才能生成支付宝请求。"""
        cid = str(customer_id)
        if cid not in self.members.index:
            raise ValueError(f"找不到会员 {cid}")
        if channel not in tasks.CHANNELS:
            raise ValueError(f"channel 必须是 {list(tasks.CHANNELS)} 之一")
        f = tasks.facts(self.members.loc[cid], self.playbook, self.playbook["stage_names"], self.playbook["tier_names"])
        state = tasks.load_state().get(cid, {})
        return _clean({"facts": f, "channel": tasks.CHANNELS[channel]["name"], "template": tasks.template_draft(f, channel),
                       "rules": [c["rule"] for c in tasks.check("", f, channel)], "task": state})

    def check_draft(self, customer_id: str, channel: str, text: str) -> dict:
        if str(customer_id) not in self.members.index:
            raise ValueError(f"找不到会员 {customer_id}")
        f = tasks.facts(self.members.loc[str(customer_id)], self.playbook, self.playbook["stage_names"], self.playbook["tier_names"])
        checks = tasks.check(text, f, channel)
        return {"checks": checks, "passed": all(c["ok"] for c in checks)}

    def update_task(self, customer_id: str, **fields) -> dict:
        cid = str(customer_id)
        if cid not in self.members.index:
            raise ValueError(f"找不到会员 {cid}")
        if fields.get("status") == "话术已确认":
            res = self.check_draft(cid, fields.get("channel") or "wecom", fields.get("text") or "")
            if not res["passed"]:
                raise ValueError("话术没有通过合规检查：" + "；".join(c["rule"] for c in res["checks"] if not c["ok"]))
        if fields.get("result") and fields["result"] not in tasks.RESULTS:
            raise ValueError(f"结果必须是 {tasks.RESULTS} 之一")
        return tasks.update(cid, **fields)

    def prepare_alipay_for_task(self, customer_id: str) -> dict:
        """导购确认话术后，生成对应的支付宝接口请求（不发送）：订阅消息提醒，积分类权益附带会员卡更新。"""
        cid = str(customer_id)
        state = tasks.load_state().get(cid, {})
        if state.get("status") not in ("话术已确认", "已生成支付宝请求", "已联系"):
            raise ValueError("话术还没有由导购确认，不能生成支付宝请求")
        f = tasks.facts(self.members.loc[cid], self.playbook, self.playbook["stage_names"], self.playbook["tier_names"])
        reqs = [alipay.subscribe_message(cid, [f"{f['常去专柜']}会员关怀", state.get("text", "")[:20], "欢迎到柜"])]
        if "积分" in f["适用权益"]:
            reqs.append(alipay.card_update(cid, point="<当前积分 + 本次礼遇积分>", reason="会员积分礼遇"))
        items = alipay.record(reqs, {"type": "导购任务", "customer_id": cid, "counter": f["常去专柜"], "text": state.get("text", "")})
        tasks.update(cid, status="已生成支付宝请求")
        return _clean({"status": alipay.STATUS, "requests": items})

    def prepare_alipay_for_campaign(self, campaign_id: str) -> dict:
        """为活动的实验组生成支付宝请求（不发送）：让利型权益建商家券活动，其他权益发订阅消息；对照组不发。"""
        rec = next((c for c in campaigns.load_all() if c["id"] == campaign_id), None)
        if rec is None:
            raise ValueError(f"找不到活动 {campaign_id}")
        treat = [k for k, v in rec["members"].items() if v[0] == "treat"]
        no_cut = actions.cfg(self.playbook, "incentive_rules")["no_price_cut_tiers"]
        start = max(pd.Timestamp(rec["start_date"]), self.snapshot)
        if rec["incentive_type"] == "price_cut":
            if rec["value_tier"] in no_cut:
                raise ValueError("高价值会员不做价格减免，不能为这个活动创建商家券")
            aov = float(self.members.loc[self.members.index.intersection(treat), "aov"].median() or 0)
            floor = max(rec["cost_per_member"] * 5, round(aov / 100) * 100)
            reqs = [alipay.voucher_activity(rec["name"], rec["cost_per_member"], floor, len(treat), start, rec["window_days"],
                                            f"满 {floor:.0f} 减 {rec['cost_per_member']:.0f}，会员专享")]
            note = f"券活动限量 {len(treat)} 张（每位实验组会员 1 张），通过订阅消息通知领取；对照组不发"
        else:
            reqs = [alipay.subscribe_message(treat[0], [rec["name"][:20], rec["action"][:20], "欢迎到柜"])] if treat else []
            note = f"订阅消息按会员逐条发送，这里展示第 1 条，共 {len(treat)} 位实验组会员；对照组不发"
        items = alipay.record(reqs, {"type": "活动", "campaign_id": campaign_id, "campaign": rec["name"],
                                     "treat_members": len(treat), "note": note})
        return _clean({"status": alipay.STATUS, "note": note, "requests": items})

    def get_outbox(self, limit: int = 50) -> dict:
        box = alipay.load_outbox()
        return _clean({"status": alipay.STATUS, "capabilities": alipay.CAPABILITIES, "total": len(box),
                       "items": list(reversed(box))[:limit]})

    def get_model_report(self) -> dict:
        return self.report

    # ---------- 内部 ----------

    def _code(self, kind: str, value: str) -> str:
        names = self.playbook["stage_names" if kind == "stage" else "tier_names"]
        if value in names:
            return value
        reverse = {v: k for k, v in names.items()}
        if value in reverse:
            return reverse[value]
        raise ValueError(f"{kind} 取值无效：{value}，可选 {list(names)} 或 {list(names.values())}")


# ---------- 给 LLM 的工具定义 ----------

TOOL_LABELS = {
    "get_overview": "查看会员整体概况",
    "segment_breakdown": "按维度拆分会员",
    "find_members": "圈选会员名单",
    "get_member_profile": "读取会员画像",
    "get_strategy": "查询运营策略",
    "simulate_campaign": "测算活动投入产出",
    "get_budget_plan": "查看预算与 ROI",
    "get_value_portrait": "查看高价值画像与拉新来源",
    "create_campaign": "登记活动并留对照组",
    "list_campaigns": "查看活动与对照实验",
    "evaluate_campaign": "评估活动增量效果",
    "get_weekly_brief": "生成本周简报",
    "list_guide_tasks": "查看导购任务",
    "get_draft_context": "准备话术素材",
    "get_action_plan": "查看本周行动计划",
    "get_model_report": "核对预测准确度",
}

_STAGES = list(load_playbook()["stage_names"])
_STAGE_DESC = " / ".join(f"{k} {v}" for k, v in load_playbook()["stage_names"].items())
_TIERS = ["high", "mid", "low"]

TOOL_SPECS = [
    {
        "name": "get_overview",
        "description": "会员整体概况：总会员数、近30天活跃、累计收入、预测未来收入、流失风险金额、各生命周期阶段的人数和收入占比，以及会员与非会员的客单价和收入占比对比（数据里有非会员流水时）。分析任何问题前通常先调用它。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "segment_breakdown",
        "description": "按某个维度拆分会员，返回每组的人数、收入、客单价、平均流失概率、流失风险金额、优惠敏感度等。用于找出问题集中在哪类人群。",
        "input_schema": {
            "type": "object",
            "properties": {"dimension": {"type": "string", "enum": list(DIMENSIONS),
                                         "description": "拆分维度：" + "，".join(f"{k}={v}" for k, v in DIMENSIONS.items())}},
            "required": ["dimension"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_members",
        "description": "按条件筛选会员名单并排序，返回匹配人数、合计风险金额和前 N 位会员（含流失原因、推荐策略、下一步动作和日期、单人成本上限）。用于圈选运营人群。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": _STAGES, "description": "生命周期：" + _STAGE_DESC},
                "value_tier": {"type": "string", "enum": _TIERS, "description": "价值分层：high / mid / low"},
                "rfm_segment": {"type": "string", "description": "RFM 八类之一，如 重要价值客户、重要挽留客户"},
                "min_churn_prob": {"type": "number", "description": "最低流失概率 0~1"},
                "incentive_group": {"type": "string", "enum": list(actions.INCENTIVE_GROUPS),
                                    "description": "权益分组：" + "，".join(f"{k}={v}" for k, v in actions.INCENTIVE_GROUPS.items())},
                "shortfall": {"type": "boolean", "description": "true=只看付费未达预期的会员"},
                "action_type": {"type": "string", "enum": list(actions.ACTION_NAMES),
                                "description": "下一步动作：" + "，".join(f"{k}={v}" for k, v in actions.ACTION_NAMES.items())},
                "sort_by": {"type": "string", "enum": sorted(SORT_FIELDS), "description": "排序字段，默认 value_at_risk（流失风险金额）"},
                "limit": {"type": "integer", "description": "返回人数，1~50，默认 10"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_member_profile",
        "description": "单个会员的完整画像：生命周期、价值及变化、品类偏好、人口属性、消费行为、优惠敏感度、预测指标、流失信号、推荐策略和适用权益、补货周期、下一步动作。",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string", "description": "会员 ID"}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_strategy",
        "description": "查询运营 Playbook 中某个 生命周期×价值分层 人群的标准策略（目标、动作、渠道、默认权益与价格敏感会员权益、KPI、频控），并附上该人群的规模、风险、价格敏感人数、单人成本上限和下一步动作分布。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": _STAGES},
                "value_tier": {"type": "string", "enum": _TIERS},
            },
            "required": ["stage", "value_tier"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simulate_campaign",
        "description": "估算针对某人群做活动的用户 ROI：目标会员预测消费 × 实际毛利率（按各自折扣率折算）vs 真实成本（每人固定成本 + 触达/导购人工的动作投入 + 权益：满减按面额全额、赠品按成本价）。返回 ROI、每位核销者最多能给多少权益、毛利抵不过成本的人数，并提示高价值降价、羊毛党、超成本上限、ROI 为负等问题。给方案时用它算账。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": _STAGES},
                "value_tier": {"type": "string", "enum": _TIERS},
                "cost_per_member": {"type": "number", "description": "每位核销者的权益面额或标价（金额）"},
                "redemption_rate": {"type": "number", "description": "权益核销率 0~1，默认 1"},
                "touches": {"type": "integer", "description": "每人触达次数，默认 1"},
                "incentive_type": {"type": "string", "enum": ["gift", "price_cut", "experience"],
                                   "description": "gift=赠品/小样（按成本价计），price_cut=满减折扣（按面额计），experience=体验服务；默认 gift"},
                "incentive_group": {"type": "string", "enum": list(actions.INCENTIVE_GROUPS), "description": "只测算某个权益分组，如 deal_hunter 羊毛党"},
            },
            "required": ["stage", "value_tier", "cost_per_member"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_budget_plan",
        "description": "运营预算与用户 ROI：各 阶段×价值 人群一年的真实运营成本、预测毛利、ROI 和建议（保持加投/压缩/削减），整体 ROI 是否为正，高价值会员占成本和毛利的比例，羊毛党里有多少因为毛利抵不过发券成本而不发券。被问到'钱花得值不值''预算怎么分''羊毛党要不要发券'时调用。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_value_portrait",
        "description": "高价值会员画像：哪些特征（年龄、性别、首单品牌、首单金额、入会年限等）在高价值会员里明显集中（TGI），高价值 vs 整体的消费特征，按首单品牌和首单金额看拉新来源质量，以及高潜新会员人数。被问到'高价值会员是什么样''拉新往哪里倾斜'时调用。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_action_plan",
        "description": "本周行动计划：每位会员只安排一个下一步动作（人工回访/补货提醒/并入大促/自动触达/暂不打扰/仅大促），返回各类人数和风险金额、导购人工产能与积压周数、补货窗口人数、近期大促档期、频控规则和月度收入季节性。被问到'这周先做什么''导购先联系谁''什么时候发'时调用。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_campaign",
        "description": "登记一个要上线的活动：从某个 阶段×价值 人群中按风险金额分层随机留出对照组（活动期间不触达），记录每人的预测消费作为基线，并给出以这个样本量能测出的最小消费提升。只有用户明确要求创建或上线活动时才调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "活动名称"},
                "stage": {"type": "string", "enum": _STAGES},
                "value_tier": {"type": "string", "enum": _TIERS},
                "action": {"type": "string", "description": "活动做什么，如：导购回访 + 到柜护理体验"},
                "cost_per_member": {"type": "number", "description": "每位核销者的权益面额或标价"},
                "incentive_type": {"type": "string", "enum": ["gift", "price_cut", "experience"]},
                "redemption_rate": {"type": "number", "description": "预计核销率 0~1，默认 0.3"},
                "incentive_group": {"type": "string", "enum": list(actions.INCENTIVE_GROUPS)},
                "window_days": {"type": "integer", "description": "观察天数，默认 90"},
                "holdout_pct": {"type": "number", "description": "对照组比例，默认 0.1"},
            },
            "required": ["name", "stage", "value_tier", "action", "cost_per_member"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_campaigns",
        "description": "列出已登记的活动：人群、上线和结束日期、实验组/对照组人数、能测出的最小提升，以及已评估的增量结果。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "evaluate_campaign",
        "description": "评估活动效果：对比实验组和对照组的'实际消费 − 预测消费'，得出每人额外消费、95% 置信区间、是否显著、增量毛利、增量成本和增量 ROI，以及结论（继续加码/压缩成本/不建议继续）。被问到'活动有没有用''钱有没有带来额外消费'时调用。",
        "input_schema": {
            "type": "object",
            "properties": {"campaign_id": {"type": "string", "description": "活动 ID，用 list_campaigns 查询"}},
            "required": ["campaign_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_weekly_brief",
        "description": "本周简报：本周要做的事（导购任务、人工产能、高价值未达预期及原因、近期档期）、钱花得怎样（用户 ROI、高价值占比、羊毛党不发券）、需要店长决定的事（待评估活动、尚未做实验）以及导购执行进度。被问到'这周怎么安排''给我个简报'时调用。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_guide_tasks",
        "description": "本周导购任务：人工回访和未达预期跟进，按会员常去的专柜分配，返回每个专柜的任务数、执行状态，以及任务明细（会员、原因、权益分组、状态、导购回填的结果）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "counter": {"type": "string", "description": "专柜名，如 LANCOME"},
                "status": {"type": "string", "enum": tasks.STATUSES},
                "limit": {"type": "integer", "description": "返回条数，默认 20"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_draft_context",
        "description": "为某位会员起草导购话术前，取得可用的事实（阶段、价值层、常去专柜、距上次到柜天数、原因、适用权益、是否禁止价格减免）、模板话术和合规规则。你可以据此写话术，但只能用这些事实；话术要由导购在看板里确认后才会生成支付宝请求。",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "channel": {"type": "string", "enum": list(tasks.CHANNELS), "description": "wecom=导购企业微信，phone=电话，alipay_msg=支付宝小程序订阅消息"},
            },
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_model_report",
        "description": "数据来源与模型可信度：数据集说明、付费预测回测误差、流失模型 AUC 与命中率、流失定义。被问到'准不准''数据从哪来'时调用。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def run_tool(store: MemberStore, name: str, args: dict) -> dict:
    fn = getattr(store, name, None)
    if fn is None or name.startswith("_") or name not in {t["name"] for t in TOOL_SPECS}:
        raise ValueError(f"未知工具 {name}")
    return fn(**args)

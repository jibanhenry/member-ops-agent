"""Agent 工具层：把管线产出的会员数据包装成可查询的函数。

每个工具返回可 JSON 序列化的 dict，既供 LLM 调用，也供 Web 接口和离线规则引擎复用。
"""

from __future__ import annotations

import json
import math
from functools import cached_property

import pandas as pd
import pyarrow.parquet as pq

from .data import PROCESSED_DIR
from .segments import load_playbook, match_strategy

MEMBERS_PATH = PROCESSED_DIR / "members.parquet"

# 不同数据集的会员档案字段不同，只暴露当前数据里存在的维度
_ALL_DIMENSIONS = {
    "stage": "生命周期阶段",
    "value_tier": "价值分层",
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
        ("stage_name", "生命周期"), ("tier_name", "价值分层"), ("rfm_segment", "RFM 分群"),
        ("gender", "性别"), ("age", "年龄"), ("reg_date", "入会时间"), ("member_years", "入会年限"),
        ("top_brand", "主要消费品牌"), ("n_brands", "购买过的品牌数"),
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
    ],
}
SORT_FIELDS = {"value_at_risk", "churn_prob", "pred_clv_365d", "monetary", "recency_days", "frequency"}


def _clean(obj):
    """NaN/numpy -> JSON 友好类型。"""
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

    def find_members(self, stage: str | None = None, value_tier: str | None = None,
                     rfm_segment: str | None = None, min_churn_prob: float | None = None,
                     sort_by: str = "value_at_risk", limit: int = 10) -> dict:
        m = self.members
        if stage:
            m = m[(m["stage"] == stage) | (m["stage_name"] == stage)]
        if value_tier:
            m = m[(m["value_tier"] == value_tier) | (m["tier_name"] == value_tier)]
        if rfm_segment:
            m = m[m["rfm_segment"] == rfm_segment]
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
        cols = ["stage_name", "tier_name", "rfm_segment", "recency_days", "frequency", "monetary",
                "churn_prob", "pred_clv_365d", "value_at_risk", "strategy", "churn_reasons"]
        top = m.sort_values(sort_by, ascending=False).head(limit)
        return _clean({
            "hint": hint,
            "matched": len(m),
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
        }
        out["流失信号"] = [x for x in (r["churn_reasons"] or "").split("；") if x]
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
            "group_avg_discount_rate": group["discount_rate"].mean(),
            "strategy": strategy,
        })

    def simulate_campaign(self, stage: str, value_tier: str, cost_per_member: float,
                          retention_uplift: float, redemption_rate: float = 1.0,
                          horizon_days: int = 90) -> dict:
        """挽留活动 ROI 估算。
        挽回收入 = Σ 流失概率 × 挽留提升率 × 年消费 × horizon/365
        成本     = 人数 × 单人权益成本 × 核销率
        """
        stage = self._code("stage", stage)
        value_tier = self._code("value_tier", value_tier)
        g = self.members[(self.members["stage"] == stage) & (self.members["value_tier"] == value_tier)]
        saved = (g["churn_prob"].fillna(0) * retention_uplift * g["amount_365d"] * horizon_days / 365).sum()
        cost = len(g) * cost_per_member * redemption_rate
        return _clean({
            "target_members": len(g),
            "expected_saved_revenue": saved,
            "expected_cost": cost,
            "roi": (saved - cost) / cost if cost else None,
            "break_even_uplift": cost / (saved / retention_uplift) if saved else None,
            "assumptions": {
                "cost_per_member": cost_per_member, "retention_uplift": retention_uplift,
                "redemption_rate": redemption_rate, "horizon_days": horizon_days,
                "说明": "挽留提升率是假设值，需用 A/B 对照组验证；挽回收入按会员近一年消费折算",
            },
            "currency": self.currency,
        })

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
    "get_model_report": "查看模型可信度",
}

_STAGES = ["new", "growing", "mature", "at_risk", "dormant", "lost"]
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
        "description": "按条件筛选会员名单并排序，返回匹配人数、合计风险金额和前 N 位会员（含流失原因和推荐策略）。用于圈选运营人群。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": _STAGES, "description": "生命周期：new 新客期 / growing 成长期 / mature 成熟期 / at_risk 衰退预警 / dormant 沉睡 / lost 流失"},
                "value_tier": {"type": "string", "enum": _TIERS, "description": "价值分层：high / mid / low"},
                "rfm_segment": {"type": "string", "description": "RFM 八类之一，如 重要价值客户、重要挽留客户"},
                "min_churn_prob": {"type": "number", "description": "最低流失概率 0~1"},
                "sort_by": {"type": "string", "enum": sorted(SORT_FIELDS), "description": "排序字段，默认 value_at_risk（流失风险金额）"},
                "limit": {"type": "integer", "description": "返回人数，1~50，默认 10"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_member_profile",
        "description": "单个会员的完整画像：生命周期、价值、品类偏好、人口属性、消费行为、优惠敏感度、预测指标、流失信号和推荐策略。",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string", "description": "会员 ID"}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_strategy",
        "description": "查询运营 Playbook 中某个 生命周期×价值分层 人群的标准策略（目标、动作、权益、KPI、频控），并附上该人群的规模和风险。",
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
        "description": "估算针对某人群做挽留/召回活动的投入产出：目标人数、预计挽回收入、成本、ROI、盈亏平衡所需的挽留提升率。给方案时用它算账。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": _STAGES},
                "value_tier": {"type": "string", "enum": _TIERS},
                "cost_per_member": {"type": "number", "description": "每人权益成本（金额）"},
                "retention_uplift": {"type": "number", "description": "活动带来的挽留提升率假设，0~1，例如 0.15"},
                "redemption_rate": {"type": "number", "description": "权益核销率 0~1，默认 1"},
                "horizon_days": {"type": "integer", "description": "收益计算窗口天数，默认 90"},
            },
            "required": ["stage", "value_tier", "cost_per_member", "retention_uplift"],
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

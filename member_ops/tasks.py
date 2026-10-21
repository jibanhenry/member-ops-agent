"""导购任务与话术：把"本周人工回访""未达预期定向触达"拆成每个专柜的任务，按会员自己的情况起草话术，
导购确认后才生成支付宝请求；联系结果只由导购回填，系统不编造结果。

- 任务归属：会员的主要消费专柜（top_brand），由该专柜导购跟进
- 话术起草：大模型只拿到运营必需的事实（阶段、价值层、常去专柜、距上次到柜天数、流失/未达预期原因、适用权益），
  不包含生日、卡号、消费明细等个人信息；没有大模型时用模板
- 合规检查：高价值会员和不发券的羊毛党不能出现价格减免；承诺金额不超过成本上限；字数受渠道限制
记录存在 data/tasks.json（不进 git）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import pandas as pd

from . import actions, economics
from .data import PROCESSED_DIR

TASKS_PATH = PROCESSED_DIR.parent / "tasks.json"
TASK_TYPES = {"manual": "人工回访", "shortfall": "未达预期跟进"}
STATUSES = ["待处理", "话术已确认", "已生成支付宝请求", "已联系"]
RESULTS = ["已到柜", "有意向", "未接通", "明确拒绝", "其他"]
CHANNELS = {"wecom": {"name": "导购企业微信", "max_len": 120},
            "phone": {"name": "电话（导购口述要点）", "max_len": 200},
            "alipay_msg": {"name": "支付宝小程序订阅消息", "max_len": 60}}
PRICE_WORDS = re.compile(r"折|满减|立减|减\s*\d|优惠券|代金券|抵扣|特价|降价|返现|红包")


def load_state() -> dict:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8")) if TASKS_PATH.exists() else {}


def save_state(state: dict) -> None:
    TASKS_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def task_frame(m: pd.DataFrame) -> pd.DataFrame:
    """本周导购任务：人工回访 + 未达预期跟进，按风险金额排序。"""
    t = m[m["action_type"].isin(list(TASK_TYPES))].copy()
    t["task_type"] = t["action_type"].map(TASK_TYPES)
    t["counter"] = t["top_brand"].fillna("未知专柜") if "top_brand" in t.columns else "门店"
    state = load_state()
    t["status"] = [state.get(k, {}).get("status", "待处理") for k in t.index]
    t["result"] = [state.get(k, {}).get("result", "") for k in t.index]
    return t.sort_values("value_at_risk", ascending=False)


# ---------- 话术 ----------

def facts(r: pd.Series, pb: dict, stage_names: dict, tier_names: dict) -> dict:
    """起草话术用到的事实：只放运营必需的信息，不含生日、卡号、手机号和消费明细。"""
    strategy = actions.match_strategy(r["stage"], r["value_tier"], pb) or {}
    reasons = (r.get("shortfall_reasons") or "") if r.get("action_type") == "shortfall" else (r.get("churn_reasons") or "")
    deal_no_coupon = r.get("coupon_decision") == economics.NO_COUPON
    no_price_cut = r["incentive_group"] == "no_price_cut" or deal_no_coupon
    return {
        "会员": r.name,
        "生命周期": stage_names.get(r["stage"], r["stage"]),
        "价值分层": tier_names.get(r["value_tier"], r["value_tier"]),
        "常去专柜": r.get("top_brand") or "门店",
        "距上次到柜天数": int(r["recency_days"]),
        "平时到柜间隔天数": None if pd.isna(r.get("mean_gap_days")) else int(r["mean_gap_days"]),
        "需要关注的原因": [x for x in reasons.split("；") if x][:3],
        "运营策略": strategy.get("name", ""),
        "适用权益": actions.pick_incentive(strategy, r["incentive_group"]) or "",
        "禁止价格减免": bool(no_price_cut),
        "单人成本上限": float(r.get("cost_cap") or 0),
    }


def template_draft(f: dict, channel: str) -> str:
    brand = f["常去专柜"]
    perk = "我们为老朋友预留了到柜护理体验和新品试用" if f["禁止价格减免"] else (f["适用权益"] or "店里准备了会员礼遇")
    if channel == "alipay_msg":
        return f"{brand}会员关怀：新品到店，{perk[:20]}"
    return f"您好，我是{brand}的专属导购，有段时间没见到您了，想问问之前的产品用得怎么样？{perk}，这周方便的话欢迎来柜台坐坐，我帮您预留时间。"


SYSTEM = """你是百货商场专柜的资深导购，负责给会员写一条一对一的关怀话术。要求：
- 口吻真诚自然，像熟人发消息，不要营销腔，不要感叹号堆砌
- 只能使用给出的事实，不要编造会员姓名、购买的具体商品、投诉内容或任何没有给出的信息
- 天数、到柜间隔、流失原因、价值分层这些是给你判断语气和重点用的，不能写进话术里告诉会员，否则会让人感觉被监视
- 如果"禁止价格减免"为 true，绝对不能出现折扣、满减、优惠券、立减、特价等价格优惠，只能用体验、新品、专属服务
- 提到的权益只能来自"适用权益"
- 直接输出话术正文，不要解释，不要加引号"""


def ai_prompt(f: dict, channel: str) -> str:
    c = CHANNELS[channel]
    facts_text = json.dumps({k: v for k, v in f.items() if k not in ("会员", "单人成本上限")}, ensure_ascii=False)
    return f"渠道：{c['name']}，字数不超过 {c['max_len']} 字。\n会员事实：{facts_text}\n请写话术。"


def check(text: str, f: dict, channel: str) -> list[dict]:
    """发出前的合规检查，任何一项不通过都要导购修改后才能确认。"""
    out = []
    hit = PRICE_WORDS.findall(text)
    out.append({"rule": "高价值会员、不发券的羊毛党不出现价格减免",
                "ok": not (f["禁止价格减免"] and hit), "detail": "出现：" + "、".join(sorted(set(hit))) if f["禁止价格减免"] and hit else ""})
    amounts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*元", text)]
    over = [a for a in amounts if a > f["单人成本上限"]]
    out.append({"rule": "承诺的金额不超过单人成本上限", "ok": not over,
                "detail": f"{over[0]:.0f} 元 > 上限 {f['单人成本上限']:.0f} 元" if over else ""})
    limit = CHANNELS[channel]["max_len"]
    out.append({"rule": f"{CHANNELS[channel]['name']}不超过 {limit} 字", "ok": len(text) <= limit, "detail": f"{len(text)} 字"})
    tracked = re.search(r"\d+\s*天|流失|沉睡|衰退|高价值|低价值|价值分层|消费记录", text)
    out.append({"rule": "不向会员透露天数、分层、流失判断等分析数据", "ok": not tracked,
                "detail": f"出现：{tracked.group(0)}" if tracked else ""})
    leak = re.search(r"\d{11}|2088\d{12}|\d{6}(19|20)\d{2}", text)
    out.append({"rule": "不包含手机号、支付宝账号、证件号等个人信息", "ok": not leak, "detail": "疑似个人信息" if leak else ""})
    out.append({"rule": "不为空", "ok": bool(text.strip()), "detail": ""})
    return out


def update(customer_id: str, **fields) -> dict:
    state = load_state()
    rec = state.setdefault(customer_id, {"status": "待处理"})
    rec.update({k: v for k, v in fields.items() if v is not None})
    rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)
    return rec


# ---------- 每周简报 ----------

REASON_CATEGORIES = [("没有到柜", "检查点后没有到柜"), ("沉默", "沉默超过平时间隔"), ("品牌", "购买品牌变少"),
                     ("客单", "客单下降"), ("折扣", "只在打折时才来")]


def reason_category(text: str) -> str | None:
    return next((name for key, name in REASON_CATEGORIES if key in (text or "")), None)


def weekly_facts(store) -> dict:
    """智能体每周主动汇总：要做的事、钱花得怎样、哪些会员掉队、哪些活动该评估、导购执行进度。"""
    plan = store.get_action_plan()
    budget = economics.budget_plan(store.members, store.playbook)
    t = task_frame(store.members)
    camps = store.list_campaigns()["campaigns"]
    due = [c["name"] for c in camps if c["mode"] == "live" and pd.Timestamp(c["end_date"]) <= store.snapshot and not c.get("result")]
    running = [c["name"] for c in camps if c["mode"] == "live" and pd.Timestamp(c["end_date"]) > store.snapshot]
    m = store.members
    high_short = m[(m["action_type"] == "shortfall") & (m["value_tier"] == "high")]
    return {
        "观察日": plan["snapshot_date"],
        "下一步动作": {a["name"]: a["members"] for a in plan["actions"]},
        "导购任务": {"总数": len(t), "按专柜前5": t["counter"].value_counts().head(5).to_dict(),
                     "执行进度": t["status"].value_counts().to_dict(), "已回填结果": t.loc[t["result"] != "", "result"].value_counts().to_dict()},
        "人工产能": plan["manual"],
        "高价值未达预期": {"人数": len(high_short),
                           "主要原因": high_short["shortfall_reasons"].str.split("；").explode().map(reason_category)
                           .value_counts().head(3).to_dict()},
        "近期档期": plan["upcoming_events"][:2],
        "预算": {"整体用户ROI": budget["overall_roi"], "运营成本": budget["total_cost"],
                 "高价值占成本": budget["high_value_cost_share"], "高价值占毛利": budget["high_value_profit_share"],
                 "羊毛党不发券": budget["deal_hunters_no_coupon"]},
        "活动": {"待评估": due, "进行中": running, "已登记": len(camps)},
    }


def template_brief(f: dict, currency: str = "CNY") -> str:
    money = lambda v: f"¥{v:,.0f}" if currency == "CNY" else f"{v:,.0f} {currency}"
    t, mn, b = f["导购任务"], f["人工产能"], f["预算"]
    counters = "、".join(f"{k} {v} 人" for k, v in list(t["按专柜前5"].items())[:3])
    reasons = "、".join(f"{k}（{v} 人）" for k, v in f["高价值未达预期"]["主要原因"].items()) or "—"
    events = "；".join(f"{e['name']} {e['start'][5:]} 起（{e['days_until']} 天后）" for e in f["近期档期"]) or "未配置档期"
    lines = [
        f"## 本周要做的事（观察日 {f['观察日']}）",
        f"- 导购任务 {t['总数']:,} 条，最多的专柜：{counters}。人工回访需求 {mn['demand']:,} 人，产能每周 {mn['capacity_per_week']} 人，"
        f"{mn['overflow_to_auto']:,} 人已转自动触达。",
        f"- 高价值会员里有 {f['高价值未达预期']['人数']} 人付费没达到预期，主要原因：{reasons}。建议导购本周优先跟进。",
        f"- 近期档期：{events}。档期前 14 天内的自动挽留已并入大促推送，不单独让利。",
        "## 钱花得怎样",
        f"- 一年运营成本 {money(b['运营成本'])}，整体用户 ROI {b['整体用户ROI']:.1f} 倍；高价值会员占成本 {b['高价值占成本']:.0%}、占预测毛利 {b['高价值占毛利']:.0%}。",
        f"- 羊毛党中 {b['羊毛党不发券']} 人毛利抵不过发券成本，本周不发券。",
        "## 需要你决定的",
    ]
    todo = []
    if f["活动"]["待评估"]:
        todo.append(f"- 活动已结束待评估：{'、'.join(f['活动']['待评估'])}。")
    if not f["活动"]["已登记"]:
        todo.append("- 还没有登记过活动。用户 ROI 只说明会员值不值得花钱，建议本周挑一个人群上线活动并留对照组，验证钱有没有带来额外消费。")
    elif f["活动"]["进行中"]:
        todo.append(f"- 进行中的活动：{'、'.join(f['活动']['进行中'])}，对照组会员活动期间不触达。")
    pending = t["执行进度"].get("待处理", 0)
    todo.append(f"- 导购任务还有 {pending:,} 条待处理；话术由智能体起草，导购确认后才会生成支付宝请求。")
    return "\n".join(lines + todo)

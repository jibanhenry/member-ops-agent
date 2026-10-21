"""支付宝开放平台适配：把运营动作翻译成支付宝开放接口请求。

当前没有对接真实商家（没有 app_id、密钥，也没有会员授权得到的支付宝 user_id），所以只生成请求、不发送：
每条请求都标注"未对接商家，未发送"，记录到 data/outbox.json，可在看板里查看。对接时只需实现 send()。

用到的接口（依据支付宝开放平台文档，字段以开放平台最新文档为准）：
- 商家券 2.0：alipay.marketing.activity.ordervoucher.create —— 创建券活动，会员在小程序里领券、支付时核销
- 小程序订阅消息：alipay.open.app.mini.templatemessage.send —— 给订阅过模板的会员发提醒
- 商家会员卡：alipay.marketing.card.update —— 更新会员卡积分、等级（如积分礼遇、等级保级）
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime

import pandas as pd

from .data import PROCESSED_DIR

GATEWAY = "https://openapi.alipay.com/gateway.do"
OUTBOX_PATH = PROCESSED_DIR.parent / "outbox.json"
STATUS = "未对接商家，未发送"

API_NAMES = {
    "alipay.marketing.activity.ordervoucher.create": "创建商家券活动",
    "alipay.open.app.mini.templatemessage.send": "小程序订阅消息",
    "alipay.marketing.card.update": "会员卡更新",
}

# 运营动作 -> 支付宝能力，用于看板说明和智能体回答
CAPABILITIES = [
    {"action": "补货提醒、未达预期提醒、活动通知", "capability": "小程序订阅消息",
     "api": "alipay.open.app.mini.templatemessage.send", "需要": "会员在商家小程序订阅过消息模板；会员授权得到支付宝 user_id"},
    {"action": "让利型权益（只给价格敏感会员、算得过账的羊毛党）", "capability": "商家券 2.0",
     "api": "alipay.marketing.activity.ordervoucher.create", "需要": "商家开通商家券；券在小程序领取、支付时核销"},
    {"action": "积分礼遇、等级保级（高价值会员的非价格权益）", "capability": "商家会员卡",
     "api": "alipay.marketing.card.update", "需要": "会员领取过商家会员卡，拿到支付宝卡号"},
    {"action": "活动效果回收", "capability": "商家券查询",
     "api": "alipay.marketing.activity.user.queryvoucher", "需要": "按会员查询领券和核销情况，回填到对照实验评估"},
]


def _envelope(method: str, biz_content: dict) -> dict:
    return {
        "gateway": GATEWAY,
        "common_params": {"app_id": "<商家应用 app_id>", "method": method, "format": "json", "charset": "UTF-8",
                          "sign_type": "RSA2", "version": "1.0", "timestamp": "<发送时生成>", "sign": "<发送时用商家私钥签名>"},
        "biz_content": biz_content,
    }


def user_id_placeholder(customer_id: str) -> str:
    return f"<会员 {customer_id} 的支付宝 user_id，需会员授权后获得>"


def subscribe_message(customer_id: str, keywords: list[str], page: str = "pages/member/index") -> dict:
    """订阅消息只能填模板里的关键词，话术正文由导购在企业微信或电话里说；关键词控制在 20 字以内。"""
    data = {f"keyword{i + 1}": {"value": v[:20]} for i, v in enumerate(keywords)}
    return _envelope("alipay.open.app.mini.templatemessage.send", {
        "to_user_id": user_id_placeholder(customer_id),
        "user_template_id": "<商家在小程序后台申请的消息模板 id>",
        "page": page,
        "data": json.dumps(data, ensure_ascii=False),
    })


def card_update(customer_id: str, point: str | None = None, level: str | None = None, reason: str = "") -> dict:
    """point 是更新后的积分总数（接口要求传绝对值）。"""
    card_info = {}
    if point is not None:
        card_info["point"] = point
    if level:
        card_info["level"] = level
    return _envelope("alipay.marketing.card.update", {
        "target_card_no": f"<会员 {customer_id} 的支付宝会员卡号>",
        "target_card_no_type": "BIZ_CARD",
        "occur_time": "<发送时间>",
        "card_info": card_info,
        "notify_messages": [{"change_reason": reason[:30]}] if reason else [],
    })


def voucher_activity(name: str, amount: float, floor_amount: float, quantity: int, start: pd.Timestamp, days: int,
                     description: str) -> dict:
    """满减券活动：floor_amount 门槛、amount 减免金额。只给价格敏感会员和算得过账的羊毛党。"""
    end = start + pd.Timedelta(days=days)
    fmt = lambda t, h: t.strftime("%Y-%m-%d ") + h
    return _envelope("alipay.marketing.activity.ordervoucher.create", {
        "out_biz_no": "mo" + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3),
        "merchant_access_mode": "SELF_MODE",
        "activity_base_info": {"activity_name": name[:30], "code_mode": "MERCHANT_API"},
        "voucher_send_mode_info": {"voucher_send_mode": "DIRECT_SEND_MODE", "voucher_send_rule_info": {
            "publish_start_time": fmt(start, "00:00:00"), "publish_end_time": fmt(end, "23:59:59"),
            "quantity": int(quantity), "quantity_limit_per_user": 1}},
        "voucher_deduct_info": {"voucher_type": "FIX_VOUCHER",
                                "fix_voucher_info": {"amount": f"{amount:.2f}", "floor_amount": f"{floor_amount:.2f}"}},
        "voucher_available_scope_info": {"voucher_available_geography_scope_info": {
            "available_geography_scope_type": "SHOP", "available_geography_shop_info": {"available_shop_ids": ["<商家门店 id>"]}}},
        "voucher_use_rule_info": {"voucher_use_time_info": {"period_type": "ABSOLUTE", "absolute_period_info": {
            "valid_begin_time": fmt(start, "00:00:00"), "valid_end_time": fmt(end, "23:59:59")}}},
        "voucher_customer_guide_info": {"voucher_use_guide_info": {
            "use_guide_mode": ["MINI_APP"], "mini_app_use_guide_info": {"mini_app_url": "<商家小程序领券页链接>"}}},
        "voucher_display_pattern_info": {"brand_name": "<商家品牌名>", "brand_logo": "<商家 logo 素材 id>",
                                         "voucher_description": description[:50]},
    })


# ---------- 发件箱 ----------

def load_outbox() -> list[dict]:
    return json.loads(OUTBOX_PATH.read_text(encoding="utf-8")) if OUTBOX_PATH.exists() else []


def record(requests: list[dict], context: dict) -> list[dict]:
    """记录生成的请求。没有对接商家，不调用 send()。"""
    box = load_outbox()
    now = datetime.now().isoformat(timespec="seconds")
    items = [{"id": "r" + secrets.token_hex(4), "created_at": now, "status": STATUS,
              "api": r["common_params"]["method"], "api_name": API_NAMES.get(r["common_params"]["method"], ""),
              "context": context, "request": r} for r in requests]
    box.extend(items)
    OUTBOX_PATH.write_text(json.dumps(box, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return items


def send(item: dict) -> dict:
    raise NotImplementedError("尚未对接真实商家：需要商家 app_id、应用私钥、支付宝公钥，以及会员授权后的支付宝 user_id")

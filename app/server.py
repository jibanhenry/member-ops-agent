"""Web 服务：会员运营看板 + 对话式 Agent。

运行：uv run uvicorn app.server:app --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from member_ops import tasks
from member_ops.agent import MemberOpsAgent
from member_ops.tools import MemberStore, run_tool

STATIC = Path(__file__).parent / "static"

store = MemberStore()
agent = MemberOpsAgent(store)
app = FastAPI(title="会员运营 Agent")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _tool(name: str, **args):
    try:
        return run_tool(store, name, {k: v for k, v in args.items() if v is not None})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"llm": agent.backend is not None, "backend": agent.label, "dataset": store.report["dataset"]}


@app.get("/api/overview")
def overview():
    return _tool("get_overview")


@app.get("/api/matrix")
def matrix():
    """生命周期 × 价值分层 矩阵：人数、风险金额、匹配策略。"""
    m = store.members
    pb = store.playbook
    cells = (
        m.groupby(["stage", "value_tier"])
        .agg(members=("frequency", "size"), value_at_risk=("value_at_risk", "sum"),
             avg_churn_prob=("churn_prob", "mean"), strategy=("strategy", "first"))
        .reset_index()
        .to_dict("records")
    )
    return {"stages": pb["stage_names"], "tiers": pb["tier_names"],
            "cells": [{k: (None if isinstance(v, float) and v != v else v) for k, v in c.items()} for c in cells]}


@app.get("/api/segments")
def segments(dimension: str = "stage"):
    return _tool("segment_breakdown", dimension=dimension)


@app.get("/api/members")
def members(stage: str | None = None, value_tier: str | None = None, action_type: str | None = None,
            incentive_group: str | None = None, shortfall: bool | None = None,
            sort_by: str = "value_at_risk", limit: int = 20, offset: int = 0):
    return _tool("find_members", stage=stage, value_tier=value_tier, action_type=action_type,
                 incentive_group=incentive_group, shortfall=shortfall, sort_by=sort_by, limit=limit, offset=offset)


EXPORT_COLUMNS = {"stage_name": "生命周期", "tier_name": "价值分层", "recency_days": "距上次消费天数", "frequency": "消费次数",
                  "monetary": "累计消费", "churn_prob": "流失概率", "value_at_risk": "流失风险金额",
                  "incentive_group_name": "权益分组", "churn_reasons": "流失信号", "next_action": "下一步动作",
                  "next_action_date": "动作日期"}


@app.get("/api/members/export")
def export_members(stage: str | None = None, value_tier: str | None = None, action_type: str | None = None,
                   incentive_group: str | None = None, shortfall: bool | None = None):
    """按与名单相同的筛选条件导出全部会员（CSV，带 BOM 方便 Excel 打开中文）。"""
    try:
        m = store.filter_members(stage=stage, value_tier=value_tier, action_type=action_type,
                                 incentive_group=incentive_group, shortfall=shortfall)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cols = [c for c in EXPORT_COLUMNS if c in m.columns]
    df = m.sort_values("value_at_risk", ascending=False)[cols].rename(columns=EXPORT_COLUMNS)
    df.index.name = "会员"
    for c in ("累计消费", "流失风险金额"):
        if c in df:
            df[c] = df[c].round(0)
    if "流失概率" in df:
        df["流失概率"] = df["流失概率"].round(3)
    csv = "\ufeff" + df.to_csv()
    return Response(csv, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''member-list.csv"})


@app.get("/api/members/{customer_id}")
def member(customer_id: str):
    return _tool("get_member_profile", customer_id=customer_id)


@app.get("/api/strategy")
def strategy(stage: str, value_tier: str):
    return _tool("get_strategy", stage=stage, value_tier=value_tier)


@app.get("/api/action-plan")
def action_plan():
    return _tool("get_action_plan")


@app.get("/api/budget-plan")
def budget_plan():
    return _tool("get_budget_plan")


@app.get("/api/value-portrait")
def value_portrait():
    return _tool("get_value_portrait")


class CampaignRequest(BaseModel):
    name: str
    stage: str
    value_tier: str
    action: str
    cost_per_member: float
    incentive_type: str = "gift"
    redemption_rate: float = 0.3
    incentive_group: str | None = None
    window_days: int = 90
    holdout_pct: float = 0.1


@app.get("/api/campaigns")
def list_campaigns():
    return _tool("list_campaigns")


@app.post("/api/campaigns")
def create_campaign(req: CampaignRequest):
    try:
        return store.create_campaign(**req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/campaigns/demo")
def demo_campaign():
    """在付费预期检查点回放一个没有真实执行的活动，检验评估方法（两组差距应接近 0）。"""
    cp = store.report.get("expectation", {}).get("checkpoint_date")
    if not cp:
        raise HTTPException(status_code=400, detail="管线没有生成付费预期检查点，无法回放")
    try:
        rec = store.create_campaign("回放演示：高价值衰退预警导购回访", "at_risk", "high", "导购回访 + 到柜护理体验",
                                    300, "experience", 0.4, window_days=store.report["expectation"]["days"], start_date=cp)
        return store.evaluate_campaign(rec["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/campaigns/{campaign_id}/evaluate")
def evaluate_campaign(campaign_id: str):
    return _tool("evaluate_campaign", campaign_id=campaign_id)


@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str):
    try:
        return store.delete_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- 智能体工作台 ----------

def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/brief")
def brief():
    return _tool("get_weekly_brief")


@app.post("/api/brief/ai")
def brief_ai():
    """让大模型把简报改写成店长能直接读的版本；数字只能来自事实。"""
    b = store.get_weekly_brief()
    text, source = agent.complete(
        "你是会员运营智能体，每周一给门店店长写一份行动简报。要求：结论先行，最多 6 条，每条一句话；"
        "所有数字只能来自给出的事实，不要编造；最后单列'需要你决定的事'。用中文，使用 Markdown 小标题和列表。",
        "本周事实：" + json.dumps(b["facts"], ensure_ascii=False, default=str))
    return {"brief": text or b["brief"], "source": source if text else f"{source}，已显示模板简报"}


@app.get("/api/tasks")
def guide_tasks(counter: str | None = None, status: str | None = None, limit: int = 30):
    return _tool("list_guide_tasks", counter=counter, status=status, limit=limit)


class DraftRequest(BaseModel):
    channel: str = "wecom"
    use_ai: bool = True


@app.post("/api/tasks/{customer_id}/draft")
def draft(customer_id: str, req: DraftRequest):
    """起草话术：有大模型时按会员事实生成，否则用模板；返回合规检查结果，导购可修改后确认。"""
    ctx = _tool("get_draft_context", customer_id=customer_id, channel=req.channel)
    text, source = (None, "模板")
    if req.use_ai:
        text, source = agent.complete(tasks.SYSTEM, tasks.ai_prompt(ctx["facts"], req.channel))
    if not text:
        source = "模板" if not req.use_ai else f"模板（{source}）"
        text = ctx["template"]
    return {"text": text, "source": source, "facts": ctx["facts"], "channel": ctx["channel"],
            **store.check_draft(customer_id, req.channel, text)}


class DraftCheck(BaseModel):
    channel: str = "wecom"
    text: str = ""


@app.post("/api/tasks/{customer_id}/check")
def check_draft(customer_id: str, req: DraftCheck):
    """导购修改话术时实时做合规检查。"""
    if req.channel not in tasks.CHANNELS:
        raise HTTPException(status_code=400, detail="未知渠道")
    return _call(store.check_draft, customer_id, req.channel, req.text)


class TaskUpdate(BaseModel):
    status: str | None = None
    channel: str | None = None
    text: str | None = None
    result: str | None = None
    note: str | None = None


@app.post("/api/tasks/{customer_id}")
def update_task(customer_id: str, req: TaskUpdate):
    return _call(store.update_task, customer_id, **req.model_dump())


@app.post("/api/tasks/{customer_id}/alipay")
def task_alipay(customer_id: str):
    return _call(store.prepare_alipay_for_task, customer_id)


@app.post("/api/campaigns/{campaign_id}/alipay")
def campaign_alipay(campaign_id: str):
    return _call(store.prepare_alipay_for_campaign, campaign_id)


@app.get("/api/outbox")
def outbox(limit: int = 50):
    return store.get_outbox(limit)


@app.get("/api/model-report")
def model_report():
    return store.report


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    return agent.chat(req.session_id, req.message)


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """NDJSON 事件流：一行一个事件，前端边收边渲染。"""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    events = (json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in agent.chat_stream(req.session_id, req.message))
    return StreamingResponse(events, media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

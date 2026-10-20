"""Web 服务：会员运营看板 + 对话式 Agent。

运行：uv run uvicorn app.server:app --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
            sort_by: str = "value_at_risk", limit: int = 20):
    return _tool("find_members", stage=stage, value_tier=value_tier, action_type=action_type, sort_by=sort_by, limit=limit)


@app.get("/api/members/{customer_id}")
def member(customer_id: str):
    return _tool("get_member_profile", customer_id=customer_id)


@app.get("/api/strategy")
def strategy(stage: str, value_tier: str):
    return _tool("get_strategy", stage=stage, value_tier=value_tier)


@app.get("/api/action-plan")
def action_plan():
    return _tool("get_action_plan")


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

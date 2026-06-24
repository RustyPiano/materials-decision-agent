"""FastAPI 应用与最小接口 (Spec §9.1)。"""
from __future__ import annotations

import asyncio
import json
import queue
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import db
from app.agent import agent
from app.config import LLMSettings
from app.runtime import SESSION


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    SESSION.restore()                 # §11.10: 恢复最近一次会话游标
    yield


app = FastAPI(title="ReSe2 Dendrite Decision Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5188", "http://127.0.0.1:5188"],  # B10: 收敛 CORS
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    s = LLMSettings()
    return {"ok": True, "llm_configured": s.configured, "model": s.model}


# ---- Campaign ----
@app.get("/api/campaign/state")
def campaign_state() -> dict:
    return SESSION.state()


class SeekReq(BaseModel):
    checkpoint: int


@app.post("/api/campaign/seek")
def campaign_seek(req: SeekReq) -> dict:
    try:
        SESSION.seek(req.checkpoint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SESSION.state()


# ---- Model & Knowledge 区页面分析 (§5.2, 只读, 非 Agent 工具循环) ----
@app.get("/api/models/compare")
def models_compare() -> dict:
    from app.science import tools as T
    return T.compare_models(SESSION.checkpoint, list(SESSION.extra_revealed))


# ---- Agent ----
class RunReq(BaseModel):
    user_goal: str = "诊断当前实验进展, 并在合适时给出下一批实验建议。"


@app.post("/api/agent/run")
def agent_run(req: RunReq) -> dict:
    s = LLMSettings()
    if not s.configured:
        raise HTTPException(status_code=503, detail="LLM 未配置 (.env)")
    run_id, _ = agent.start_run(req.user_goal)
    return {"run_id": run_id, "checkpoint": SESSION.checkpoint}


@app.get("/api/agent/{run_id}/events")
async def agent_events(run_id: str) -> EventSourceResponse:
    handle = agent.REGISTRY.get(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def gen():
        while True:
            try:
                ev = handle.events.get_nowait()
            except queue.Empty:
                if handle.done.is_set():
                    break
                await asyncio.sleep(0.1)
                continue
            yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}
            if ev["type"] == "done":
                break

    return EventSourceResponse(gen())


class ApproveReq(BaseModel):
    action: str = "approve"          # approve | modify
    candidate_ids: list[int] | None = None


@app.post("/api/agent/{run_id}/approve")
def agent_approve(run_id: str, req: ApproveReq) -> dict:
    try:
        return agent.approve(run_id, req.action, req.candidate_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    except agent.ApprovalConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except agent.ApprovalInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agent/{run_id}/reject")
def agent_reject(run_id: str) -> dict:
    try:
        return agent.reject(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")
    except agent.ApprovalConflict as e:
        raise HTTPException(status_code=409, detail=str(e))

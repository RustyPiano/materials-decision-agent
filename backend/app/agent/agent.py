"""LangGraph Agent 闭环 (Spec §6)。

- StateGraph: agent(LLM 原生 tool-calling) ⇄ tools, ReAct 循环 (§6.2)。
- 工具结果流式推送 (§6.1 streaming)。
- 人工审批门: 图在 emit_decision_card 处结束并置 pending_decision, 由 API 层
  接收 approve/reject 后揭示真实 D_F (等价于 interrupt; §4.2 step6-7)。
- §9.3: 记录 provider/model/system_prompt_hash/tool_list_version/token 用量/结构化输出。

审核加固 (REVIEW_M1 B1/B2/B4/B5):
- 决策卡 suggested_candidate_ids 必须 ⊆ 本 run 实际 suggest_next_batch 返回的候选 (§6.5)。
- 只接受首张决策卡; 零卡片终止则合成 no_action 卡, 始终保留审批门。
- approve/reject 基于 run 快照、加终态守卫、校验 checkpoint 未变。
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app import db
from app.agent.tooldefs import SYSTEM_PROMPT, TOOL_LIST_VERSION, TOOL_SCHEMAS, dispatch
from app.config import LLMSettings
from app.data import campaign as C
from app.runtime import SESSION

MAX_STEPS = 8
MAX_EMPTY_RETRIES = 2     # 推理模型经代理偶发空补全(0 输出 token, 无 tool_call); 有界重试再兜底
LLM_TOOL_OBS_LIMIT = 6000
SYSTEM_PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


class ApprovalConflict(Exception):
    """审批状态冲突 → HTTP 409。"""


class ApprovalInvalid(Exception):
    """审批请求非法 → HTTP 400。"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    run_id: str
    campaign_id: str
    checkpoint: int
    extra_revealed: list[int]
    step_count: int
    empty_streak: int          # 连续"无 tool_call"回合数, 用于有界重试空补全
    pending_decision: Optional[dict]


@dataclass
class RunHandle:
    run_id: str
    checkpoint: int
    extra_revealed: list[int]
    events: queue.Queue = field(default_factory=queue.Queue)
    last_status: Optional[str] = None
    last_candidate_ids: list[int] = field(default_factory=list)  # 最近 suggest_next_batch 的候选, 用于校验
    status: str = "running"
    decision_card: Optional[dict] = None
    suggested_ids: list[int] = field(default_factory=list)       # 已校验的卡内候选子集
    token_usage: dict = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)

    def push(self, ev: dict) -> None:
        self.events.put(ev)


REGISTRY: dict[str, RunHandle] = {}

_llm: Optional[ChatOpenAI] = None
_llm_lock = threading.Lock()


def get_llm() -> ChatOpenAI:
    global _llm
    with _llm_lock:
        if _llm is None:
            s = LLMSettings()
            if not s.configured:
                raise RuntimeError("LLM 未配置: 请在 .env 设置 OPENAI_BASE_URL / OPENAI_API_KEY")
            kwargs = dict(base_url=s.base_url, api_key=s.api_key, model=s.model,
                          timeout=60, max_retries=2)
            # 推理模型(claude / o 系)拒绝 temperature 参数 → 仅对支持的模型发送
            # basename 归一(去 provider 前缀)+ 小写; o 系用 o\d 匹配 o1..o9
            mid = s.model.split("/")[-1].lower()
            if not (mid.startswith("claude") or re.match(r"o\d", mid)):
                kwargs["temperature"] = s.temperature
            _llm = ChatOpenAI(**kwargs).bind_tools(TOOL_SCHEMAS)
    return _llm


# ---- 图节点 ----
def _agent_node(state: AgentState) -> dict:
    ai = get_llm().invoke(state["messages"])
    handle = REGISTRY.get(state["run_id"])
    if handle and getattr(ai, "usage_metadata", None):
        um = ai.usage_metadata
        handle.token_usage = {
            "input": handle.token_usage.get("input", 0) + um.get("input_tokens", 0),
            "output": handle.token_usage.get("output", 0) + um.get("output_tokens", 0),
        }
    empty = not getattr(ai, "tool_calls", None)   # 无工具调用 = 空补全或纯文本(本设计须经工具结束)
    return {"messages": [ai], "step_count": state["step_count"] + 1,
            "empty_streak": state["empty_streak"] + 1 if empty else 0}


def _normalize_card(args: dict, allowed_ids: list[int]) -> dict:
    """规整决策卡; suggested_candidate_ids 仅保留 ⊆ 本 run 实际候选的 (B1/B4)。"""
    raw_ids = args.get("suggested_candidate_ids", []) or []
    valid = [i for i in raw_ids if i in allowed_ids]
    dropped = [i for i in raw_ids if i not in allowed_ids]
    evidence = list(args.get("evidence", []))
    if dropped:
        evidence.append(f"[系统校验] 丢弃非本次建议的候选 id: {dropped}")
    return {
        "status": args.get("status"),
        "evidence": evidence,
        "recommendation": args.get("recommendation", ""),
        "alternatives": args.get("alternatives", []),
        "uncertainty": args.get("uncertainty", ""),
        "suggested_candidate_ids": valid,
        "approval_required": True,
    }


def _tools_node(state: AgentState) -> dict:
    last = state["messages"][-1]
    handle = REGISTRY[state["run_id"]]
    out: list = []
    decision = state.get("pending_decision")
    for tc in last.tool_calls:
        name, args, tcid = tc["name"], tc.get("args", {}), tc["id"]
        if name == "emit_decision_card":
            if decision is not None:  # 只接受首张卡 (B4)
                out.append(ToolMessage(content="已有决策卡, 忽略重复调用", tool_call_id=tcid))
                continue
            decision = _normalize_card(args, handle.last_candidate_ids)
            db.log_decision(state["run_id"], decision)
            handle.push({"type": "decision_card", "card": decision})
            out.append(ToolMessage(content="决策卡已记录", tool_call_id=tcid))
            continue
        handle.push({"type": "tool_call", "tool": name, "args": args})
        # 幻觉/未知工具或工具内异常不得崩 run: 回纠正性观测让模型自纠 (claude 偶发自创工具名)
        try:
            result = dispatch(name, args, state["checkpoint"], state["extra_revealed"], handle.last_status)
        except Exception as e:  # noqa: BLE001
            valid = [s["function"]["name"] for s in TOOL_SCHEMAS]
            err = {"tool": name, "error": f"{type(e).__name__}: {e}", "valid_tools": valid}
            db.log_tool_run(state["run_id"], state["step_count"], name, args, err)
            handle.push({"type": "tool_result", "tool": name, "result": err})
            out.append(ToolMessage(
                content=json.dumps({"error": err["error"], "请改用以下工具之一": valid}, ensure_ascii=False),
                tool_call_id=tcid))
            continue
        if name == "assess_progress":
            handle.last_status = result.get("status")
        if name == "suggest_next_batch":
            handle.last_candidate_ids = [c["candidate_id"] for c in result.get("candidates", [])]
        db.log_tool_run(state["run_id"], state["step_count"], name, args, result)
        handle.push({"type": "tool_result", "tool": name, "result": result})
        # 给 LLM 的观测剥离仅供前端绘图的大数组(oof), 并保证合法 JSON (B7)
        llm_result = {k: v for k, v in result.items() if k != "oof"}
        obs = json.dumps(llm_result, ensure_ascii=False, allow_nan=False)
        if len(obs) > LLM_TOOL_OBS_LIMIT:
            obs = json.dumps({"truncated": True, "tool": name,
                              "summary": result.get("verdict") or result.get("status") or "见 UI 审计面板"},
                             ensure_ascii=False)
        out.append(ToolMessage(content=obs, tool_call_id=tcid))
    return {"messages": out, "pending_decision": decision}


def _nudge_node(state: AgentState) -> dict:
    """空/无工具回合后的纠正提示 (应对代理偶发空补全), 推回 agent 重试。"""
    return {"messages": [HumanMessage(
        "上一步未调用任何工具。请直接调用工具继续诊断, 并最终调用一次 emit_decision_card 结束。"
    )]}


def _route_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    # 空补全/纯文本回合: 步数内有界重试, 超限才结束(交由零卡片兜底)
    if state["empty_streak"] <= MAX_EMPTY_RETRIES and state["step_count"] < MAX_STEPS:
        return "nudge"
    return END


def _route_tools(state: AgentState) -> str:
    if state.get("pending_decision"):
        return END
    if state["step_count"] >= MAX_STEPS:
        return END
    return "agent"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", _agent_node)
    g.add_node("tools", _tools_node)
    g.add_node("nudge", _nudge_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", _route_agent, {"tools": "tools", "nudge": "nudge", END: END})
    g.add_edge("nudge", "agent")
    g.add_conditional_edges("tools", _route_tools, {"agent": "agent", END: END})
    return g.compile()


GRAPH = build_graph()


def _fallback_card(handle: RunHandle) -> dict:
    """零卡片终止兜底: 始终保留审批门, 默认不行动 (B4 + §4.3)。"""
    return {
        "status": handle.last_status or "uncertain",
        "evidence": ["Agent 在最大步数内未产出决策卡, 系统按'不行动'兜底"],
        "recommendation": "不执行, 等待更多数据",
        "alternatives": [],
        "uncertainty": "Agent 未完成结构化决策",
        "suggested_candidate_ids": [],
        "approval_required": True,
    }


# ---- 运行编排 ----
def start_run(user_goal: str) -> tuple[str, RunHandle]:
    run_id = uuid.uuid4().hex[:12]
    handle = RunHandle(run_id=run_id, checkpoint=SESSION.checkpoint,
                       extra_revealed=list(SESSION.extra_revealed))
    REGISTRY[run_id] = handle

    s = LLMSettings()
    db.log_agent_run(run_id, SESSION.campaign_id, SESSION.checkpoint, user_goal,
                     "openai_compatible", s.model, SYSTEM_PROMPT_HASH, TOOL_LIST_VERSION)

    def _runner() -> None:
        try:
            inputs: AgentState = {
                "messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(user_goal)],
                "run_id": run_id, "campaign_id": SESSION.campaign_id,
                "checkpoint": handle.checkpoint, "extra_revealed": handle.extra_revealed,
                "step_count": 0, "empty_streak": 0, "pending_decision": None,
            }
            final = GRAPH.invoke(inputs, config={"recursion_limit": 3 * MAX_STEPS + 4})
            card = final.get("pending_decision")
            if card is None:  # 零卡片兜底, 保留审批门 (B4)
                card = _fallback_card(handle)
                db.log_decision(run_id, card)
                handle.push({"type": "decision_card", "card": card})
            handle.decision_card = card
            handle.suggested_ids = card.get("suggested_candidate_ids", [])
            handle.status = "awaiting_approval"
            db.update_agent_run(run_id, handle.status, handle.token_usage)
            handle.push({"type": "awaiting_approval", "card": card, "token_usage": handle.token_usage})
        except Exception as e:  # noqa: BLE001
            handle.status = "error"
            db.update_agent_run(run_id, "error", handle.token_usage)
            handle.push({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            handle.push({"type": "done"})
            handle.done.set()

    threading.Thread(target=_runner, daemon=True).start()
    return run_id, handle


def _revealed_details(ids: list[int]) -> list[dict]:
    return [
        {**e.features(), "y1": e.y1, "exp_id": e.exp_id, "source": e.source}
        for e in C.experiments_by_id(ids)
    ]


def approve(run_id: str, action: str, candidate_ids: list[int] | None = None) -> dict:
    handle = REGISTRY.get(run_id)
    if handle is None:
        raise KeyError(run_id)
    if handle.status != "awaiting_approval":
        raise ApprovalConflict(f"run 状态为 {handle.status}, 不可审批")
    if SESSION.checkpoint != handle.checkpoint:
        raise ApprovalConflict("当前 checkpoint 已与运行时不同, 请在原 checkpoint 审批或重跑")

    if action == "modify":
        if not candidate_ids:
            raise ApprovalInvalid("modify 需提供候选 id 子集")
        invalid = [i for i in candidate_ids if i not in handle.last_candidate_ids]
        if invalid:
            raise ApprovalInvalid(f"候选 id 非本次建议, 已拒绝: {invalid}")
        ids = candidate_ids
    else:  # approve: 用卡内已校验的候选 (可能为空 = 确认不行动)
        ids = handle.suggested_ids

    newly = SESSION.reveal(ids) if ids else []
    handle.status = "approved"
    db.update_agent_run(run_id, "approved", handle.token_usage)
    db.log_approval(run_id, action, {"requested_ids": ids}, newly)
    return {"action": action, "revealed": _revealed_details(newly),
            "no_action": not ids, "campaign_state": SESSION.state()}


def reject(run_id: str) -> dict:
    handle = REGISTRY.get(run_id)
    if handle is None:
        raise KeyError(run_id)
    if handle.status != "awaiting_approval":
        raise ApprovalConflict(f"run 状态为 {handle.status}, 不可拒绝")
    handle.status = "rejected"
    db.update_agent_run(run_id, "rejected", handle.token_usage)
    db.log_approval(run_id, "reject", {}, [])
    return {"action": "reject", "campaign_state": SESSION.state()}

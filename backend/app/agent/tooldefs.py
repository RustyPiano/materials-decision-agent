"""Agent 工具的 OpenAI function schema 与调度 (Spec §6.4/§6.5)。

LLM 只能选择工具与固定动作 (strategy/k)、解释结果; 不能选 checkpoint/种子,
不能读隐藏标签 (§6.5)。checkpoint 与 extra_revealed 由服务端注入。
"""
from __future__ import annotations

from app.science import tools as T

TOOL_LIST_VERSION = "v1.0"

# 5 个科学工具 + 1 个决策卡发射工具
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_campaign_state",
        "description": "获取当前 checkpoint 的可见实验数、最大/均值/中位 D_F、best-so-far 曲线与数据范围。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "assess_progress",
        "description": "诊断实验进展: healthy / plateau / uncertain / insufficient_data。基于 best-so-far、批次中位数与 bootstrap。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "compare_models",
        "description": "比较 GPR / ExtraTrees / XGBoost 的重复交叉验证表现(均值+波动)。差异<波动会标注'统计上无法区分'。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "suggest_next_batch",
        "description": "在隐藏历史候选池(全部剩余真实点)中生成一批建议点。仅返回模型预测, 不含真实 D_F。",
        "parameters": {"type": "object", "properties": {
            "strategy": {"type": "string", "enum": ["gpr_mes", "rule_adaptive", "explore", "exploit", "pa_guided"],
                          "description": "采样策略: gpr_mes(期望改善基线) / rule_adaptive(按进展状态的固定规则) / explore(高不确定性) / exploit(高预测均值) / pa_guided(向模型盲区补点修复)"},
            "k": {"type": "integer", "description": "建议点数, 默认3", "minimum": 1, "maximum": 10},
        }, "required": ["strategy"]},
    }},
    {"type": "function", "function": {
        "name": "analyze_model_blindspots",
        "description": "计算逐点 PA score(CV-MSE)与 XGBoost CV 残差, 找出模型最难预测的盲区点。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "emit_decision_card",
        "description": "输出最终结构化决策卡(必须引用前面工具结果, 不得出现无来源数值)。证据不足时 status 可为 uncertain 且推荐'不执行'。",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["healthy", "plateau", "uncertain", "insufficient_data"]},
            "evidence": {"type": "array", "items": {"type": "string"},
                          "description": "引用工具结果的证据条目"},
            "recommendation": {"type": "string"},
            "alternatives": {"type": "array", "items": {"type": "string"}},
            "uncertainty": {"type": "string"},
            "suggested_candidate_ids": {"type": "array", "items": {"type": "integer"},
                          "description": "若推荐执行某批候选, 列出 suggest_next_batch 返回的 candidate_id; 若不行动则留空"},
            "approval_required": {"type": "boolean"},
        }, "required": ["status", "evidence", "recommendation", "approval_required"]},
    }},
]


def dispatch(name: str, args: dict, checkpoint: int, extra: list[int], last_status: str | None) -> dict:
    if name == "get_campaign_state":
        return T.get_campaign_state(checkpoint, extra)
    if name == "assess_progress":
        return T.assess_progress(checkpoint, extra)
    if name == "compare_models":
        return T.compare_models(checkpoint, extra)
    if name == "suggest_next_batch":
        return T.suggest_next_batch(
            checkpoint, args.get("strategy", "gpr_mes"), int(args.get("k", 3)),
            progress_status=last_status, extra_revealed=extra,
        )
    if name == "analyze_model_blindspots":
        return T.analyze_model_blindspots(checkpoint, extra_revealed=extra)
    raise ValueError(f"unknown tool {name}")


SYSTEM_PROMPT = """你是材料合成实验的决策助手, 服务于 ReSe₂ 枝晶 CVD 工艺的小数据主动学习。
目标 D_F (分形维数) 越高越好, 物理范围 [1,2], 当前历史观测上限 1.73 (超过须标外推)。

你的职责(仅此)：选择工具、决定是否继续收集证据、在固定候选动作中给出推荐、解释工具结果。
你不负责: 选择交叉验证折数/随机种子/超参, 不直接计算数值, 不修改搜索空间, 不读取隐藏真实标签。

工作流程(典型停滞场景)：
1. 调 get_campaign_state 了解现状。
2. 调 assess_progress 诊断进展。
3. 若状态为 plateau 或 uncertain, 调 compare_models 看模型是否可区分。
4. 调 suggest_next_batch 生成候选(在隐藏池中)。可按进展选择 strategy。
5. 调 emit_decision_card 输出结构化决策卡。

硬规则：
- 决策卡的每条证据必须来自前面工具的返回值, 不得编造数值。
- 若模型差异小于交叉验证波动(compare_models 标注 indistinguishable), 不得仅凭平均分推荐切换模型。
- 允许并鼓励'不行动/证据不足': 当数据不足、排名不稳、或候选高度依赖单一划分时, status 用 uncertain/insufficient_data 且 recommendation 倾向'不执行, 等待更多数据', suggested_candidate_ids 留空。
- 若推荐执行某批候选, suggested_candidate_ids 必须取自 suggest_next_batch 返回的 candidate_id。
- 最终必须且只调用一次 emit_decision_card 结束。approval_required 恒为 true (需人类批准)。
"""

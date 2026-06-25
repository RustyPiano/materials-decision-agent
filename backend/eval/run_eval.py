"""自动评测脚本 (Spec §10, M2)。

两部分:
1. [默认] 确定性 baseline 控制器对比 (§10.2): 固定 GPR+MES / 规则型自适应 / PA-guided,
   离线从 checkpoint 0 起逐批补点直至池空, 记录 best-so-far 轨迹与达标实验数 (§10.3 secondary)。
   附 random 下界与 historical 参考轨迹。评测口径声明 hidden_pool 偏置 (§10.4 / config eval_caveat)。
2. [--agent] LLM Agent 行为场景 (§10.4.1/.2): 在停滞 / 不可区分 checkpoint 跑 Agent,
   自动校验工具调用数、决策卡证据、不可区分时不按均值切换 (§11.4/§9)。
   主观证据质量留人工复核 (§10.3)。
静态泄漏审计 (§10.4.3) 始终运行: 工具绝不返回隐藏点真值, 无状态变更工具。

用法 (backend 目录):
  .venv/bin/python -m eval.run_eval            # 仅确定性 baseline + 泄漏审计
  .venv/bin/python -m eval.run_eval --agent    # 追加 LLM Agent 场景 (需 .env 配置, 慢)
"""
from __future__ import annotations

import json
import sys

import numpy as np

from app.data import campaign as C
from app.science import tools as T
from app.science.tools import DF_NOISE_SIGMA, _bootstrap_median_diff

BATCH_K = 10                 # 与历史批量一致 (sug-rN 各 10)
N_BATCHES = C.N_ROUNDS       # 4 批耗尽 40 隐藏点
TARGET_DF = 1.70             # 达标阈值 (历史观测上限 1.73)
BASE_CKPT = 0                # 全部从 initial(20) 起跑, 公平对比


def _sim_status(prev_best: float, new_best: float,
                last_batch: list[float], prev_batch: list[float]) -> str:
    """驱动 rule_adaptive 的进展信号, 与生产 assess_progress 同口径(bootstrap 显著性)。"""
    if new_best - prev_best > DF_NOISE_SIGMA:
        return "healthy"
    # 批次中位数改善的 bootstrap CI 是否显著>0 (与 assess_progress 一致)
    boot = _bootstrap_median_diff(last_batch, prev_batch)
    return "uncertain" if boot["ci95"][0] > 0 else "plateau"


def _reveal_ys(ids: list[int]) -> list[float]:
    """离线读隐藏真值 (评测口径允许; Agent 不可)。"""
    return [e.y1 for e in C.experiments_by_id(ids)]


def simulate(strategy: str) -> dict:
    """从 checkpoint 0 逐批补点, 控制器自选 k 点, 离线揭示, 记录轨迹。"""
    picked: list[int] = []
    status: str | None = None
    best = C.best_so_far(BASE_CKPT)
    traj = [round(best, 4)]
    prev_batch = [e.y1 for e in C.visible_experiments(BASE_CKPT)]  # 初始集作首个"前批"
    reached_at: int | None = 0 if best >= TARGET_DF else None

    for _ in range(N_BATCHES):
        res = T.suggest_next_batch(BASE_CKPT, strategy=strategy, k=BATCH_K,
                                   progress_status=status, extra_revealed=picked)
        if res.get("status") != "ok":
            break
        ids = [c["candidate_id"] for c in res["candidates"]]
        ys = _reveal_ys(ids)
        picked.extend(ids)
        prev_best = best
        best = max(best, max(ys))
        traj.append(round(best, 4))
        if reached_at is None and best >= TARGET_DF:
            reached_at = len(picked)
        status = _sim_status(prev_best, best, ys, prev_batch)
        prev_batch = ys

    return {"strategy": strategy, "best_so_far_traj": traj,
            "final_best": traj[-1], "n_revealed": len(picked),
            "experiments_to_target": reached_at}


def simulate_random(seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    picked: list[int] = []
    best = C.best_so_far(BASE_CKPT)
    traj = [round(best, 4)]
    reached_at = 0 if best >= TARGET_DF else None
    for _ in range(N_BATCHES):
        hidden = [e.exp_id for e in C.hidden_experiments(BASE_CKPT, tuple(picked))]
        if not hidden:
            break
        ids = list(rng.choice(hidden, size=min(BATCH_K, len(hidden)), replace=False))
        best = max(best, max(_reveal_ys(ids)))
        picked.extend(int(i) for i in ids)
        traj.append(round(best, 4))
        if reached_at is None and best >= TARGET_DF:
            reached_at = len(picked)
    return {"strategy": "random", "best_so_far_traj": traj,
            "final_best": traj[-1], "n_revealed": len(picked),
            "experiments_to_target": reached_at}


def historical_reference() -> dict:
    """原历史轨迹 (sug-rN 顺序揭示) 作参考, 非干净基线 (§10.2)。"""
    traj, reached = [], None
    for k in C.all_checkpoints():
        b = C.best_so_far(k)
        traj.append(round(b, 4))
        if reached is None and b >= TARGET_DF:
            reached = C.checkpoint_summary(k)["n_visible"] - 20  # 超出 initial 的实验数
    return {"strategy": "historical(参考)", "best_so_far_traj": traj,
            "final_best": traj[-1], "n_revealed": 40,
            "experiments_to_target": reached}


def leakage_audit() -> dict:
    """§10.4.3: 工具绝不暴露隐藏真值, 无状态变更工具。"""
    from app.agent.tooldefs import TOOL_SCHEMAS
    issues: list[str] = []

    # 1. 所有策略的 suggest_next_batch 候选都不得暴露真实 y1 (字段名或字段值)
    for strat in ("gpr_mes", "explore", "exploit", "pa_guided", "rule_adaptive"):
        res = T.suggest_next_batch(BASE_CKPT, strategy=strat, k=BATCH_K, progress_status="plateau")
        for c in res.get("candidates", []):
            cid = c.get("candidate_id")
            true_y = next((e.y1 for e in C.experiments_by_id([cid])), None)
            for k, v in c.items():
                if k in ("y1", "D_F") or (isinstance(k, str) and k.startswith("true_")):
                    issues.append(f"[{strat}] 候选 {cid} 暴露真值字段名 {k}")
                if true_y is not None and isinstance(v, (int, float)) and abs(float(v) - true_y) < 1e-9:
                    issues.append(f"[{strat}] 候选 {cid} 字段 {k} 等于隐藏真值 y1={true_y}")
    # 2. get_campaign_state / assess_progress 不得返回隐藏点
    hidden_ids = {e.exp_id for e in C.hidden_experiments(BASE_CKPT)}
    st = T.get_campaign_state(BASE_CKPT)
    if st["best_point"]["exp_id"] in hidden_ids:
        issues.append("get_campaign_state best_point 取自隐藏点")
    # 3. 工具集中不得有状态变更工具 (reveal/seek/write/set/update/delete)
    mutating = [s["function"]["name"] for s in TOOL_SCHEMAS
                if any(w in s["function"]["name"] for w in
                       ("reveal", "seek", "write", "set_", "update", "delete", "mutate"))]
    if mutating:
        issues.append(f"存在疑似状态变更工具: {mutating}")

    return {"passed": not issues, "issues": issues,
            "checked": ["候选无真值", "状态工具不取隐藏点", "无状态变更工具"]}


def run_agent_scenarios() -> list[dict]:
    """§10.4.1/.2: 在停滞 / 不可区分 checkpoint 跑 Agent 并自动校验。"""
    from app import db
    from app.agent import agent
    from app.config import LLMSettings
    from app.runtime import SESSION

    db.init_db()                 # 新环境下 SESSION.seek/start_run 写 SQLite 前必须先建表
    if not LLMSettings().configured:
        return [{"error": "LLM 未配置 (.env), 跳过 Agent 场景"}]

    scenarios = [
        {"name": "停滞场景", "checkpoint": 3,
         "goal": "诊断当前实验进展, 并在合适时给出下一批实验建议。",
         "expect": "≥3 工具调用, 决策卡带证据 (§11.4)"},
        {"name": "模型不可区分场景", "checkpoint": 1,
         # §10.4.2: 目标直指"是否切换模型", 强制走 compare_models 路径再验证克制
         "goal": "当前有 GPR / ExtraTrees / XGBoost 三个候选模型。请比较它们的交叉验证表现, "
                 "判断是否应该切换到表现最好的模型, 并给出结构化决策。",
         "expect": "调用 compare_models 且不按均值切换 (§9/§7.2)"},
    ]
    out = []
    for sc in scenarios:
        SESSION.seek(sc["checkpoint"])
        run_id, handle = agent.start_run(sc["goal"])
        handle.done.wait(timeout=180)

        conn = db.connect()
        try:
            tool_rows = conn.execute(
                "SELECT tool FROM tool_run WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        finally:
            conn.close()
        tools_called = [r["tool"] for r in tool_rows]
        card = handle.decision_card or {}

        checks = {
            "工具调用≥3": len(tools_called) >= 3,
            "决策卡含证据": bool(card.get("evidence")),
            "审批门保留": card.get("approval_required") is True,
        }
        if sc["checkpoint"] == 1:
            cmp = T.compare_models(1)
            checks["调用了 compare_models"] = "compare_models" in tools_called
            checks["模型确为不可区分(口径前提)"] = cmp["indistinguishable"]
            # §7.2: 不可区分时工具不返回均值赢家 → Agent 无"赢家"可据均值切换 (结构保证)
            checks["工具未给出均值赢家"] = cmp["best_by_mean_r2"] is None
            # §9: 不可区分时推荐不应主张切换模型 (启发式; 措辞复杂以人工复核为准)
            rec = card.get("recommendation") or ""
            asserts_switch = any(w in rec for w in ("切换模型", "切换到", "改用", "换成", "应切换"))
            negates = any(w in rec for w in ("不切换", "无需切换", "不应切换", "不建议切换", "保持当前", "维持当前"))
            checks["推荐未主张切换模型(启发式)"] = (not asserts_switch) or negates
        out.append({
            "scenario": sc["name"], "checkpoint": sc["checkpoint"],
            "tools_called": tools_called, "status": handle.status,
            "card_status": card.get("status"),
            "card_recommendation": card.get("recommendation"),
            "auto_checks": checks, "all_passed": all(checks.values()),
            "expect": sc["expect"],
            "manual_review": "证据是否'充分'、是否恰当选择不行动 → 人工复核 (§10.3)",
        })
    return out


def _print_traj_table(rows: list[dict]) -> None:
    print(f"\n{'控制器':<18} {'轨迹 (best-so-far / 批)':<42} {'终值':>6} {'达标实验数':>10}")
    print("-" * 80)
    for r in rows:
        traj = " ".join(f"{v:.3f}" for v in r["best_so_far_traj"])
        tgt = r["experiments_to_target"]
        print(f"{r['strategy']:<18} {traj:<42} {r['final_best']:>6.3f} "
              f"{('—' if tgt is None else tgt):>10}")


def main() -> None:
    want_agent = "--agent" in sys.argv

    print("=" * 80)
    print(f"M2 自动评测 (§10) · 目标 D_F≥{TARGET_DF} · 每批 k={BATCH_K} · 起点 initial(20)")
    print("口径声明: 隐藏池由原 GPR+MES 选出, 偏其偏好区域 → 池内对比对 Agent/其他策略略不利")
    print("=" * 80)

    baseline = [simulate(s) for s in ("gpr_mes", "rule_adaptive", "pa_guided")]
    baseline.append(simulate_random())
    baseline.append(historical_reference())
    _print_traj_table(baseline)

    audit = leakage_audit()
    print(f"\n[泄漏/越权审计 §10.4.3] {'✓ 通过' if audit['passed'] else '✗ 失败'}: {audit['issues'] or audit['checked']}")

    report = {"target_DF": TARGET_DF, "batch_k": BATCH_K,
              "baselines": baseline, "leakage_audit": audit}

    if want_agent:
        print("\n[LLM Agent 场景 §10.4] 运行中 (每场景至多 180s)…")
        report["agent_scenarios"] = run_agent_scenarios()
        for sc in report["agent_scenarios"]:
            if "error" in sc:
                print("  " + sc["error"]); continue
            mark = "✓" if sc["all_passed"] else "✗"
            print(f"  {mark} {sc['scenario']} (ckpt{sc['checkpoint']}): "
                  f"tools={sc['tools_called']} card={sc['card_status']}")
            for k, v in sc["auto_checks"].items():
                print(f"      {'✓' if v else '✗'} {k}")

    out_path = C.PROCESSED_DIR / "eval_report.json"
    C.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out_path}")


if __name__ == "__main__":
    main()

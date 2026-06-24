"""第一版 5 个高层科学工具 (Spec §6.4)。

约束:
- 工具只用「当前可见数据」(§7.1)，绝不返回隐藏点的真实 y1 (§6.5 LLM 不得读隐藏标签)。
- compare_models 报告均值+波动；差异<波动 → "统计上无法区分" (§7.2)。
- assess_progress 仅用 best-so-far / 批次中位数 / bootstrap (§7.3)。
- suggest_next_batch 候选池 = 决策#4=B 全部剩余隐藏点；池空 → no_action (§4.3)。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from app.config import FEATURES
from app.data import campaign as C
from app.science import models as M

# D_F 测量精度 (SI: 1.71±0.03) → 用于"改善是否超过噪声"
DF_NOISE_SIGMA = 0.03
MIN_SAMPLE = 12  # 低于此判 insufficient_data


def _visible_frame(checkpoint: int, extra: list[int]) -> pd.DataFrame:
    vis = C.visible_experiments(checkpoint, tuple(extra))
    return C.to_frame(vis)


def _display(feat: dict) -> dict:
    """给 LLM/卡片的特征补上显示单位 (B6): f_H2 真实 sccm 与衬底名。"""
    return {
        **feat,
        "f_H2_sccm": round(float(feat["x4"]) * 100),
        "substrate_label": "c-Al2O3" if int(feat["x5"]) == 1 else "MgO",
    }


# ---- 1. get_campaign_state ----
def get_campaign_state(checkpoint: int, extra_revealed: list[int] | None = None) -> dict:
    extra = extra_revealed or []
    vis = C.visible_experiments(checkpoint, tuple(extra))
    summ = C.checkpoint_summary(checkpoint, tuple(extra))
    best = max(vis, key=lambda e: e.y1)
    # best-so-far 曲线 (按基础 checkpoint)
    curve = [
        {"checkpoint": k, "best_DF": round(C.best_so_far(k), 4)}
        for k in C.all_checkpoints()
        if k <= checkpoint
    ]
    ranges = {
        f: {"min": float(min(getattr(e, f) for e in vis)),
            "max": float(max(getattr(e, f) for e in vis))}
        for f in FEATURES
    }
    return {
        "tool": "get_campaign_state",
        "checkpoint": checkpoint,
        **summ,
        "best_point": {**_display(best.features()), "y1": best.y1, "exp_id": best.exp_id},
        "best_so_far_curve": curve,
        "feature_ranges_visible": ranges,
        "df_physical_range": [C.DF_MIN, C.DF_MAX],
        "extrapolation_cap": 1.73,
    }


# ---- 2. assess_progress ----
def _batch_at_round(rnd: int) -> list[float]:
    return [e.y1 for e in C.load_experiments() if e.revealed_at_round == rnd]


def _bootstrap_median_diff(cur: list[float], prev: list[float], n_boot: int = 2000) -> dict:
    rng = np.random.default_rng(0)
    cur_a, prev_a = np.array(cur), np.array(prev)
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        c = rng.choice(cur_a, size=len(cur_a), replace=True)
        p = rng.choice(prev_a, size=len(prev_a), replace=True)
        diffs[b] = np.median(c) - np.median(p)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"median_diff": float(np.median(cur_a) - np.median(prev_a)),
            "ci95": [round(float(lo), 4), round(float(hi), 4)]}


def assess_progress(checkpoint: int, extra_revealed: list[int] | None = None) -> dict:
    extra = tuple(extra_revealed or [])
    vis = C.visible_experiments(checkpoint, extra)
    n = len(vis)
    evidence: list[str] = []

    if (checkpoint == 0 and not extra) or n < MIN_SAMPLE:
        return {
            "tool": "assess_progress", "checkpoint": checkpoint,
            "status": "insufficient_data",
            "evidence": [f"可见样本 n={n}, 无前一批次可比 (checkpoint={checkpoint})"],
        }

    # B3: 所有进展统计统一基于当前可见集 (含审批揭示), 避免与页面矛盾且不读隐藏标签
    best_now = C.best_so_far(checkpoint, extra)
    if extra:                                   # 最近动作 = 审批揭示批次, 与揭示前状态比较
        best_prev = C.best_so_far(checkpoint)
        cur_batch = [e.y1 for e in C.experiments_by_id(list(extra))]
        prev_batch = _batch_at_round(checkpoint)
    else:                                       # 历史重放: 当前轮 vs 上一轮
        best_prev = C.best_so_far(checkpoint - 1)
        cur_batch = _batch_at_round(checkpoint)
        prev_batch = _batch_at_round(checkpoint - 1)
    delta_best = round(best_now - best_prev, 4)
    improved_best = delta_best > DF_NOISE_SIGMA
    evidence.append(
        f"best-so-far {best_prev:.3f}→{best_now:.3f} (Δ={delta_best:+.3f}, 噪声σ={DF_NOISE_SIGMA})"
    )

    boot = _bootstrap_median_diff(cur_batch, prev_batch)
    sig_median = boot["ci95"][0] > 0
    evidence.append(
        f"批次中位数改善={boot['median_diff']:+.3f}, bootstrap CI95={boot['ci95']} "
        f"({'显著>0' if sig_median else '与0重叠'})"
    )

    if improved_best:
        status = "healthy"
        evidence.append("最近批次刷新 best-so-far 且超过测量噪声")
    elif not sig_median:
        status = "plateau"
        evidence.append("best-so-far 未提升, 且批次中位数改善与不确定性重叠 → 停滞")
    else:
        status = "uncertain"
        evidence.append("best-so-far 未提升, 但批次中位数仍在改善 → 进展不明确")

    return {"tool": "assess_progress", "checkpoint": checkpoint, "status": status,
            "delta_best": delta_best, "batch_median": boot, "evidence": evidence}


# ---- 3. compare_models ----
def compare_models(checkpoint: int, extra_revealed: list[int] | None = None) -> dict:
    df = _visible_frame(checkpoint, extra_revealed or [])
    results = [M.repeated_cv(name, df) for name in M.MODEL_SPECS]
    ranked = sorted(results, key=lambda r: r.r2_mean, reverse=True)
    top, second = ranked[0], ranked[1]
    # 差异<波动 → 统计上无法区分
    pooled_std = math.sqrt(top.r2_std ** 2 + second.r2_std ** 2)
    indistinguishable = (top.r2_mean - second.r2_mean) < pooled_std
    verdict = (
        f"{top.model} 与 {second.model} 的 CV R² 差 "
        f"{top.r2_mean - second.r2_mean:.3f} < 合并波动 {pooled_std:.3f} → 统计上无法区分"
        if indistinguishable
        else f"{top.model} 在 CV R² 上领先 (差 {top.r2_mean - second.r2_mean:.3f} ≥ 波动 {pooled_std:.3f})"
    )
    return {
        "tool": "compare_models", "checkpoint": checkpoint,
        "cv_config": "repeated KFold 5×5 (固定种子)",
        "models": [r.summary() for r in ranked],
        # B7/§7.2: 统计无法区分时不给"赢家", 只给均值最高者作展示参考
        "best_by_mean_r2": None if indistinguishable else top.model,
        "leading_by_mean": top.model,
        "indistinguishable": indistinguishable,
        "verdict": verdict,
        "oof": {r.model: {"true": r.oof_true, "pred": r.oof_pred} for r in results},
    }


# ---- 4. suggest_next_batch ----
def _acquisition(mu: np.ndarray, std: np.ndarray, best: float, strategy: str) -> np.ndarray:
    std = np.maximum(std, 1e-9)
    if strategy in ("gpr_mes", "ei"):  # Expected Improvement (离散池上的采集函数代理; 真 MES 留 M2)
        z = (mu - best) / std
        return (mu - best) * norm.cdf(z) + std * norm.pdf(z)
    if strategy == "explore":
        return std
    if strategy == "exploit":
        return mu
    if strategy == "rule_adaptive":  # 固定规则: 见 suggest_next_batch 调用处按 status 选
        return mu  # 占位, 实际在外层替换
    raise ValueError(f"unknown strategy {strategy}")


def suggest_next_batch(
    checkpoint: int, strategy: str = "gpr_mes", k: int = 3,
    progress_status: str | None = None, extra_revealed: list[int] | None = None,
) -> dict:
    extra = extra_revealed or []
    df_vis = _visible_frame(checkpoint, extra)
    hidden = C.hidden_experiments(checkpoint, tuple(extra))
    if not hidden:
        return {"tool": "suggest_next_batch", "checkpoint": checkpoint,
                "status": "no_action", "reason": "隐藏候选池为空, 无可建议的真实点 (§4.3)"}

    X_cand = pd.DataFrame([e.features() for e in hidden])
    mu, std = M.fit_gpr_predict(df_vis, X_cand)
    best = C.best_so_far(checkpoint, tuple(extra))

    eff_strategy = strategy
    rule_note = None
    if strategy == "rule_adaptive":
        # 固定规则自适应: 停滞/不确定→偏探索(高不确定性); 健康→偏利用(高均值)
        eff_strategy = "explore" if progress_status in ("plateau", "uncertain") else "exploit"
        rule_note = f"rule_adaptive: status={progress_status} → {eff_strategy}"

    acq = _acquisition(mu, std, best, eff_strategy)
    order = np.argsort(acq)[::-1][:k]

    candidates = []
    for rank, idx in enumerate(order):
        e = hidden[idx]
        extrap = bool(mu[idx] > 1.73)
        candidates.append({
            "rank": rank + 1,
            "candidate_id": e.exp_id,           # 标识符, 用于审批后揭示; 不含真实 y1
            **_display(e.features()),
            "pred_DF": round(float(mu[idx]), 4),
            "pred_std": round(float(std[idx]), 4),
            "acquisition": round(float(acq[idx]), 5),
            "extrapolation": extrap,
        })
    return {
        "tool": "suggest_next_batch", "checkpoint": checkpoint,
        "status": "ok", "strategy": strategy, "effective_strategy": eff_strategy,
        "rule_note": rule_note, "pool_size": len(hidden), "k": k,
        "current_best": round(best, 4),
        "candidates": candidates,
        "note": "候选为隐藏池真实点的参数, 仅含模型预测; 真实 D_F 须人类批准后揭示 (§6.5)",
    }


# ---- 5. analyze_model_blindspots ----
def analyze_model_blindspots(
    checkpoint: int, top_n: int = 5, extra_revealed: list[int] | None = None
) -> dict:
    df = _visible_frame(checkpoint, extra_revealed or [])
    pa = M.pointwise_pa_score(df)
    xgb_cv = M.repeated_cv("XGBoost", df)
    return {
        "tool": "analyze_model_blindspots", "checkpoint": checkpoint,
        "metric": "PA score = 逐点 CV-MSE (Prediction Accuracy)",
        "xgb_cv_rmse": {"mean": round(xgb_cv.rmse_mean, 4), "std": round(xgb_cv.rmse_std, 4)},
        "high_error_points": pa[:top_n],
        "note": "高 PA 点 = 模型最难预测处, 是 PA-guided 补点候选区 (§6.4)",
    }

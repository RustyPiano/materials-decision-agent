"""防泄漏模型与重复交叉验证 (Spec §7.1, §7.2)。

关键约束:
- 每个 checkpoint 的模型只在「当前可见数据」上 fit (§7.1)。
- 缩放为「域边界 min-max」(非数据驱动) → 不泄漏 (M0 审计结论)。
- 重复 KFold, 报告均值+波动; 差异<波动 → 统计上无法区分 (§7.2)。
- 超参固定于 configs/science_config.yaml (§6.5)。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold
from xgboost import XGBRegressor

from app.config import FEATURES, science_config

def _gpr_cv_restarts() -> int:
    """CV 中 GPR 重启次数, 来自版本化配置 (B9; 为交互响应度低于最终单次 fit 的值)。"""
    return int(science_config()["models"]["GPR"].get("cv_n_restarts_optimizer", 4))


def _ranges() -> dict[str, list[float]]:
    return science_config()["preprocessing"]["feature_ranges"]


def scale_features(X: pd.DataFrame) -> np.ndarray:
    """域边界 min-max (非数据驱动 → 不泄漏)。用于 GPR。"""
    rng = _ranges()
    out = np.empty((len(X), len(FEATURES)), dtype=float)
    for j, f in enumerate(FEATURES):
        lo, hi = rng[f]
        span = (hi - lo) or 1.0
        out[:, j] = (X[f].to_numpy(dtype=float) - lo) / span
    return out


def raw_features(X: pd.DataFrame) -> np.ndarray:
    return X[FEATURES].to_numpy(dtype=float)


# ---- 模型工厂 (超参来自 science_config) ----
def make_gpr(n_restarts: int | None = None) -> GaussianProcessRegressor:
    cfg = science_config()["models"]["GPR"]
    return GaussianProcessRegressor(
        kernel=Matern(length_scale=1.0, nu=1.5),
        alpha=cfg.get("alpha", 0.05),
        normalize_y=cfg.get("normalize_y", True),
        n_restarts_optimizer=cfg.get("n_restarts_optimizer", 20)
        if n_restarts is None
        else n_restarts,
        random_state=0,
    )


def make_extra_trees() -> ExtraTreesRegressor:
    p = science_config()["models"]["ExtraTrees"]["params"]
    return ExtraTreesRegressor(**p)


def make_xgboost() -> XGBRegressor:
    p = science_config()["models"]["XGBoost"]["params"]
    return XGBRegressor(objective="reg:squarederror", **p)


# 模型名 -> (工厂, 是否需缩放)
MODEL_SPECS = {
    "GPR": (lambda: make_gpr(_gpr_cv_restarts()), True),
    "ExtraTrees": (make_extra_trees, False),
    "XGBoost": (make_xgboost, False),
}


def _clean(values: list[float]) -> list:
    """NaN → None, 否则四舍五入 (B7: 避免非法 JSON 字面量 NaN)。"""
    return [None if (v is None or np.isnan(v)) else round(float(v), 4) for v in values]


@dataclass
class CVResult:
    model: str
    r2_mean: float
    r2_std: float
    rmse_mean: float
    rmse_std: float
    mae_mean: float
    mae_std: float
    n: int
    oof_true: list[float]
    oof_pred: list[float]

    def summary(self) -> dict:
        return {
            "model": self.model,
            "r2": {"mean": round(self.r2_mean, 4), "std": round(self.r2_std, 4)},
            "rmse": {"mean": round(self.rmse_mean, 4), "std": round(self.rmse_std, 4)},
            "mae": {"mean": round(self.mae_mean, 4), "std": round(self.mae_std, 4)},
            "n": self.n,
        }


def _cv_config() -> tuple[int, int, list[int]]:
    cv = science_config()["cross_validation"]
    return cv["folds"], cv["repeats"], cv["random_state_per_repeat"]


def repeated_cv(model_name: str, df: pd.DataFrame) -> CVResult:
    """对单个模型在可见数据上做重复 KFold。返回均值/波动 + out-of-fold 预测。"""
    folds, repeats, seeds = _cv_config()
    factory, needs_scale = MODEL_SPECS[model_name]
    X = scale_features(df) if needs_scale else raw_features(df)
    y = df["y1"].to_numpy(dtype=float)
    n = len(y)

    r2s, rmses, maes = [], [], []
    # 取最后一个 repeat 的 oof 作为预测-实验图用
    oof_true = np.full(n, np.nan)
    oof_pred = np.full(n, np.nan)

    for ri, seed in enumerate(seeds[:repeats]):
        kf = KFold(n_splits=folds, shuffle=True, random_state=seed)
        for tr, te in kf.split(X):
            model = factory()
            model.fit(X[tr], y[tr])
            pred = model.predict(X[te])
            r2s.append(r2_score(y[te], pred))
            rmses.append(float(np.sqrt(mean_squared_error(y[te], pred))))
            maes.append(mean_absolute_error(y[te], pred))
            if ri == repeats - 1:
                oof_true[te] = y[te]
                oof_pred[te] = pred

    return CVResult(
        model=model_name,
        r2_mean=float(np.mean(r2s)), r2_std=float(np.std(r2s)),
        rmse_mean=float(np.mean(rmses)), rmse_std=float(np.std(rmses)),
        mae_mean=float(np.mean(maes)), mae_std=float(np.std(maes)),
        n=n,
        oof_true=_clean(oof_true.tolist()),
        oof_pred=_clean(oof_pred.tolist()),
    )


def pointwise_pa_score(df: pd.DataFrame) -> list[dict]:
    """PA score = 逐点 CV-MSE (M0: Prediction Accuracy, data expansion.ipynb)。

    用 XGBoost 重复 KFold, 每点累加 (y-ŷ)² 再除以重复数, 降序=最难预测=盲区。
    """
    folds, repeats, seeds = _cv_config()
    X = raw_features(df)
    y = df["y1"].to_numpy(dtype=float)
    n = len(y)
    acc = np.zeros(n)

    for seed in seeds[:repeats]:
        kf = KFold(n_splits=folds, shuffle=True, random_state=seed)
        for tr, te in kf.split(X):
            model = make_xgboost()
            model.fit(X[tr], y[tr])
            pred = model.predict(X[te])
            acc[te] += (y[te] - pred) ** 2
    pa = acc / repeats

    rows = []
    for i in range(n):
        rows.append({
            **{f: float(df.iloc[i][f]) for f in FEATURES},
            "y1": float(y[i]),
            "pa_score": round(float(pa[i]), 5),
        })
    rows.sort(key=lambda r: r["pa_score"], reverse=True)
    return rows


def fit_gpr_predict(df_visible: pd.DataFrame, X_cand: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """在可见数据上 fit GPR, 对候选点预测均值+标准差 (用于采集函数)。"""
    gpr = make_gpr()
    gpr.fit(scale_features(df_visible), df_visible["y1"].to_numpy(dtype=float))
    mu, std = gpr.predict(scale_features(X_cand), return_std=True)
    return mu, std


# ---- SHAP importance + I score (Spec §5.2, M2) ----
# 仅树模型 (§5.2 约束); 用 XGBoost 原生 TreeSHAP (pred_contribs/pred_interactions),
# 无需额外 shap 依赖。I score = 主效应/交互效应比 (science_config: XGBoost_SHAP_interaction, 正文 Eq.2)。
I_SCORE_MIN_N = 30      # ponytail: I score 仅在样本足时给确定结论 (§5.2); 原 notebook 用全量, 30 为保守门槛, 不够调高
_SHAP_BOOT = 20         # 重要度稳定性 bootstrap 次数; 嫌慢调小


def _xgb_shap(X: np.ndarray, y: np.ndarray, with_inter: bool) -> tuple:
    import xgboost as xgb
    model = make_xgboost()
    model.fit(X, y)
    booster = model.get_booster()
    dm = xgb.DMatrix(X)
    contribs = booster.predict(dm, pred_contribs=True)                       # (n, F+1) 末列=bias
    inter = booster.predict(dm, pred_interactions=True) if with_inter else None  # (n, F+1, F+1)
    return contribs, inter


def shap_explain(df: pd.DataFrame) -> dict:
    """XGBoost TreeSHAP 特征重要度 (mean|SHAP|, 带 bootstrap 稳定性) + I score。"""
    F = len(FEATURES)
    X = raw_features(df)
    y = df["y1"].to_numpy(dtype=float)
    n = len(y)

    contribs, inter = _xgb_shap(X, y, with_inter=True)
    importance = np.abs(contribs[:, :F]).mean(axis=0)

    # 重要度稳定性: bootstrap 重采样, 报告 std (§5.2 稳定性不足不出确定结论)
    rng = np.random.default_rng(0)
    boots = np.empty((_SHAP_BOOT, F))
    for b in range(_SHAP_BOOT):
        idx = rng.integers(0, n, n)
        c, _ = _xgb_shap(X[idx], y[idx], with_inter=False)
        boots[b] = np.abs(c[:, :F]).mean(axis=0)
    imp_std = boots.std(axis=0)

    # I score: 主效应=|对角| 均值, 交互效应=Σ_{j≠i}|非对角| 均值 (per feature)
    diag = inter[:, np.arange(F), np.arange(F)]                # (n, F)
    main = np.abs(diag).mean(axis=0)
    off = np.abs(inter[:, :F, :F]).sum(axis=2).mean(axis=0) - main
    i_reliable = n >= I_SCORE_MIN_N

    features = []
    for j, f in enumerate(FEATURES):
        features.append({
            "feature": f,
            "importance": round(float(importance[j]), 4),
            "importance_std": round(float(imp_std[j]), 4),
            "main_effect": round(float(main[j]), 4),
            "interaction_effect": round(float(off[j]), 4),
            # I>1 主效应主导(独立), I<1 交互主导; 样本不足(不可靠)时不给确定结论 → null (§5.2)
            "i_score": None if (not i_reliable or off[j] < 1e-9) else round(float(main[j] / off[j]), 3),
        })
    features.sort(key=lambda r: r["importance"], reverse=True)
    return {
        "tool": "shap_explain", "model": "XGBoost", "n": n,
        "features": features,
        "i_score_reliable": i_reliable,
        "i_score_note": "" if i_reliable else f"样本 n={n}<{I_SCORE_MIN_N}, I score 不稳定, 仅供参考 (§5.2)",
        "note": "TreeSHAP 关联度量, 非因果机制; 仅 XGBoost 计算 (§5.2)",
    }


def pa_guided_acquisition(df_visible: pd.DataFrame, X_cand: pd.DataFrame, top_blind: int = 5) -> np.ndarray:
    """PA-guided 补点采集: 候选离高 PA 盲区点越近分越高 (域归一距离)。"""
    blind = pointwise_pa_score(df_visible)[:top_blind]
    Xb = scale_features(pd.DataFrame([{f: b[f] for f in FEATURES} for b in blind]))
    Xc = scale_features(X_cand)
    dist = np.min(np.linalg.norm(Xc[:, None, :] - Xb[None, :, :], axis=2), axis=1)
    return -dist

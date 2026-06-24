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

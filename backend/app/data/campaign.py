"""历史 Campaign 数据层 (Spec §3.2 数据状态, §4.1 重放)。

从 vendor/ML4Dendrites 阶段1 主动学习数据构造逐 checkpoint 的
可见/隐藏切分。构造方式: initial(20) + 累积 sug-r1..r4(各10) = 60,
每个实验带 revealed_at_round, checkpoint k 的:
  - measured_visible: revealed_at_round <= k
  - measured_hidden : revealed_at_round  > k   (决策 #4=B: 全部剩余隐藏点)
已离线验证 round(N)=round(N-1)+sug-r(N)。
"""
from __future__ import annotations

import functools
import json
from dataclasses import asdict, dataclass

import pandas as pd

from app.config import FEATURES, PROCESSED_DIR, TARGET, VENDOR_DIR, data_manifest

DATASET_FILE = VENDOR_DIR / "1-Process optimization" / "Dataset_bayesian.xlsx"

# checkpoint -> (sheet 来源, revealed_at_round)。initial=round0, sug-rN 揭示于 round N。
_INITIAL_SHEET = "initial"
_SUG_SHEETS = {1: "sug-r1", 2: "sug-r2", 3: "sug-r3", 4: "sug-r4"}
N_ROUNDS = 4  # checkpoint 0..4
DF_MIN, DF_MAX = 1.0, 2.0


@dataclass(frozen=True)
class Experiment:
    exp_id: int
    x1: float
    x2: float
    x3: float
    x4: float
    x5: int
    y1: float
    revealed_at_round: int  # 0..4
    source: str             # "initial" | "sug-rN"

    def features(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in FEATURES}


def _read_sheet(sheet: str) -> pd.DataFrame:
    df = pd.read_excel(DATASET_FILE, sheet_name=sheet, engine="openpyxl")
    cols = FEATURES + [TARGET]
    df = df[[c for c in cols if c in df.columns]].copy()
    df = df.dropna(how="all").dropna(subset=[TARGET])
    return df.reset_index(drop=True)


def _validate_df_range(experiments: list[Experiment]) -> None:
    bad = [e for e in experiments if not (DF_MIN <= e.y1 <= DF_MAX)]
    if bad:
        raise ValueError(
            f"D_F 超出物理范围 [{DF_MIN},{DF_MAX}] (Spec §7.4): "
            + ", ".join(f"exp#{e.exp_id}={e.y1}" for e in bad)
        )


@functools.lru_cache(maxsize=1)
def load_experiments() -> tuple[Experiment, ...]:
    """构造 60 个带 provenance 的实验。"""
    experiments: list[Experiment] = []
    eid = 0

    init = _read_sheet(_INITIAL_SHEET)
    for _, row in init.iterrows():
        experiments.append(
            Experiment(
                exp_id=eid, revealed_at_round=0, source="initial",
                x1=float(row.x1), x2=float(row.x2), x3=float(row.x3),
                x4=float(row.x4), x5=int(row.x5), y1=float(row.y1),
            )
        )
        eid += 1

    for rnd, sheet in _SUG_SHEETS.items():
        sug = _read_sheet(sheet)
        for _, row in sug.iterrows():
            experiments.append(
                Experiment(
                    exp_id=eid, revealed_at_round=rnd, source=sheet,
                    x1=float(row.x1), x2=float(row.x2), x3=float(row.x3),
                    x4=float(row.x4), x5=int(row.x5), y1=float(row.y1),
                )
            )
            eid += 1

    _validate_df_range(experiments)
    return tuple(experiments)


def visible_experiments(
    checkpoint: int, extra_revealed: tuple[int, ...] | frozenset[int] = ()
) -> list[Experiment]:
    """可见 = 基础 checkpoint 已揭示 ∪ 审批后额外揭示的点 (§4.2 step7)。"""
    extra = set(extra_revealed)
    return [
        e for e in load_experiments()
        if e.revealed_at_round <= checkpoint or e.exp_id in extra
    ]


def hidden_experiments(
    checkpoint: int, extra_revealed: tuple[int, ...] | frozenset[int] = ()
) -> list[Experiment]:
    """决策 #4=B: 当前仍未揭示的全部真实点 (全部剩余隐藏池)。"""
    extra = set(extra_revealed)
    return [
        e for e in load_experiments()
        if e.revealed_at_round > checkpoint and e.exp_id not in extra
    ]


def experiments_by_id(ids: list[int]) -> list[Experiment]:
    by_id = {e.exp_id: e for e in load_experiments()}
    return [by_id[i] for i in ids if i in by_id]


def to_frame(experiments: list[Experiment]) -> pd.DataFrame:
    return pd.DataFrame([{**e.features(), TARGET: e.y1} for e in experiments])


def best_so_far(checkpoint: int, extra_revealed: tuple[int, ...] | frozenset[int] = ()) -> float:
    vis = visible_experiments(checkpoint, extra_revealed)
    return max(e.y1 for e in vis)


def checkpoint_summary(
    checkpoint: int, extra_revealed: tuple[int, ...] | frozenset[int] = ()
) -> dict:
    vis = visible_experiments(checkpoint, extra_revealed)
    hid = hidden_experiments(checkpoint, extra_revealed)
    ys = [e.y1 for e in vis]
    n = len(ys)
    mean = sum(ys) / n
    srt = sorted(ys)
    median = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    return {
        "checkpoint": checkpoint,
        "n_visible": n,
        "n_hidden": len(hid),
        "best_DF": round(max(ys), 4),
        "mean_DF": round(mean, 4),
        "median_DF": round(median, 4),
        "min_DF": round(min(ys), 4),
    }


def all_checkpoints() -> list[int]:
    return list(range(N_ROUNDS + 1))


def export_processed() -> dict:
    """落盘 data/processed/campaign.json 供检查 (派生物, 已 gitignore)。"""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_commit": data_manifest()["source"]["commit_sha"],
        "experiments": [asdict(e) for e in load_experiments()],
        "checkpoints": [checkpoint_summary(k) for k in all_checkpoints()],
    }
    out = PROCESSED_DIR / "campaign.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    payload = export_processed()
    print(f"commit {payload['manifest_commit'][:10]} | {len(payload['experiments'])} experiments")
    print(f"{'ckpt':>4} {'vis':>4} {'hid':>4} {'best':>6} {'mean':>6} {'median':>7} {'min':>5}")
    for c in payload["checkpoints"]:
        print(
            f"{c['checkpoint']:>4} {c['n_visible']:>4} {c['n_hidden']:>4} "
            f"{c['best_DF']:>6} {c['mean_DF']:>6} {c['median_DF']:>7} {c['min_DF']:>5}"
        )

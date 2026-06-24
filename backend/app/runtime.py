"""单用户运行态会话 (Spec §9: 单用户、本地)。

CampaignSession = 当前基础 checkpoint + 审批后额外揭示的真实点 id 集合。
seek 切换 checkpoint 并清空额外揭示; reveal 在人类批准后并入隐藏池真实点 (§4.2 step7)。
游标持久化到 SQLite, 启动时恢复 → 满足 §11.10 (REVIEW_M1 B8)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import db
from app.data import campaign as C

CAMPAIGN_ID = "reSe2-dendrite-case1"


@dataclass
class CampaignSession:
    campaign_id: str = CAMPAIGN_ID
    checkpoint: int = 1                       # 默认停在 round1 (uncertain 场景)
    extra_revealed: list[int] = field(default_factory=list)

    def _persist(self) -> None:
        db.save_session_state(self.checkpoint, self.extra_revealed)

    def restore(self) -> None:
        st = db.load_session_state()
        if st is not None:
            self.checkpoint, self.extra_revealed = st[0], list(st[1])

    def seek(self, checkpoint: int) -> None:
        if checkpoint not in C.all_checkpoints():
            raise ValueError(f"checkpoint {checkpoint} 越界 {C.all_checkpoints()}")
        self.checkpoint = checkpoint
        self.extra_revealed = []
        self._persist()

    def reveal(self, exp_ids: list[int]) -> list[int]:
        hidden_ids = {e.exp_id for e in C.hidden_experiments(self.checkpoint, tuple(self.extra_revealed))}
        newly = [i for i in exp_ids if i in hidden_ids]
        if newly:
            self.extra_revealed.extend(newly)
            self._persist()
        return newly

    def state(self) -> dict:
        summ = C.checkpoint_summary(self.checkpoint, tuple(self.extra_revealed))
        vis = C.visible_experiments(self.checkpoint, tuple(self.extra_revealed))
        extra_set = set(self.extra_revealed)
        experiments = [
            {
                **e.features(), "y1": e.y1, "exp_id": e.exp_id,
                "source": e.source,
                "revealed_at_round": e.revealed_at_round,   # F9: 重放批次 provenance
                # 数据状态 (§3.2): 审批揭示标 revealed, 基础可见标 measured_visible
                "state": "revealed" if e.exp_id in extra_set else "measured_visible",
            }
            for e in vis
        ]
        return {
            "campaign_id": self.campaign_id,
            "checkpoint": self.checkpoint,
            "all_checkpoints": C.all_checkpoints(),
            "extra_revealed": list(self.extra_revealed),
            **summ,
            "best_so_far_curve": [
                {"checkpoint": k, "best_DF": round(C.best_so_far(k), 4)}
                for k in C.all_checkpoints() if k <= self.checkpoint
            ],
            "experiments": experiments,
            "n_hidden_pool": len(C.hidden_experiments(self.checkpoint, tuple(self.extra_revealed))),
        }


# 单例会话 (M1 单用户)
SESSION = CampaignSession()

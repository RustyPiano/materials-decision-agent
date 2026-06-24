"""SQLite 持久化 (Spec §9.2)。审计四表(追加写入、可追溯) + 会话游标持久化。

审核修订 (REVIEW_M1 B8):
- 删除只写不读的 experiment/checkpoint 表(静态数据由 campaign.py 内存层提供)。
- 新增 session_state 单行表持久化 SESSION 游标(checkpoint + extra_revealed),
  启动时恢复 → 满足 §11.10 '恢复最近一次运行'。
- 启动不再 DELETE 重灌; 仅 CREATE TABLE IF NOT EXISTS。
"""
from __future__ import annotations

import json
import sqlite3
import time

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "app.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_run (
    run_id TEXT PRIMARY KEY, campaign_id TEXT, checkpoint INTEGER, user_goal TEXT,
    status TEXT, provider TEXT, model TEXT, system_prompt_hash TEXT,
    tool_list_version TEXT, token_usage TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS tool_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, seq INTEGER,
    tool TEXT, args TEXT, result TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS decision (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, card TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS approval (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, action TEXT,
    detail TEXT, revealed_ids TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    checkpoint INTEGER, extra_revealed TEXT, updated_at REAL
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _now() -> float:
    return time.time()


# ---- 会话游标持久化 (§11.10) ----
def save_session_state(checkpoint: int, extra_revealed: list[int]) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO session_state (id, checkpoint, extra_revealed, updated_at) VALUES (1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET checkpoint=excluded.checkpoint, "
            "extra_revealed=excluded.extra_revealed, updated_at=excluded.updated_at",
            (checkpoint, json.dumps(extra_revealed), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def load_session_state() -> tuple[int, list[int]] | None:
    conn = connect()
    try:
        row = conn.execute("SELECT checkpoint, extra_revealed FROM session_state WHERE id=1").fetchone()
        if row is None:
            return None
        return int(row["checkpoint"]), json.loads(row["extra_revealed"] or "[]")
    finally:
        conn.close()


# ---- 审计日志 (追加写入) ----
def log_agent_run(run_id: str, campaign_id: str, checkpoint: int, user_goal: str,
                  provider: str, model: str, sph: str, tool_version: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_run VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, campaign_id, checkpoint, user_goal, "running", provider, model,
             sph, tool_version, "{}", _now()),
        )
        conn.commit()
    finally:
        conn.close()


def update_agent_run(run_id: str, status: str, token_usage: dict | None = None) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE agent_run SET status=?, token_usage=? WHERE run_id=?",
            (status, json.dumps(token_usage or {}), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def log_tool_run(run_id: str, seq: int, tool: str, args: dict, result: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO tool_run (run_id, seq, tool, args, result, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, seq, tool, json.dumps(args, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False, default=str), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def log_decision(run_id: str, card: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO decision (run_id, card, created_at) VALUES (?,?,?)",
            (run_id, json.dumps(card, ensure_ascii=False), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def log_approval(run_id: str, action: str, detail: dict, revealed_ids: list[int]) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO approval (run_id, action, detail, revealed_ids, created_at) VALUES (?,?,?,?,?)",
            (run_id, action, json.dumps(detail, ensure_ascii=False),
             json.dumps(revealed_ids), _now()),
        )
        conn.commit()
    finally:
        conn.close()

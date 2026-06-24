"""路径解析与版本化配置加载 (Spec §6.5: 所有科学配置来自版本化文件)。"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# backend/app/config.py -> repo root = parents[2]
ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = ROOT / "configs"
VENDOR_DIR = ROOT / "vendor" / "ML4Dendrites"
DATA_DIR = ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"

load_dotenv(ROOT / ".env")


@functools.lru_cache(maxsize=None)
def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIGS_DIR / name
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def data_manifest() -> dict[str, Any]:
    return _load_yaml("data_manifest.yaml")


def science_config() -> dict[str, Any]:
    return _load_yaml("science_config.yaml")


def llm_config() -> dict[str, Any]:
    return _load_yaml("llm_config.yaml")


class LLMSettings:
    """从 .env 读取 LLM 凭据 (Spec §9: 凭据不入库)。"""

    def __init__(self) -> None:
        cfg = llm_config()
        self.base_url = os.environ.get(cfg.get("base_url_env", "OPENAI_BASE_URL"))
        self.api_key = os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY"))
        self.model = os.environ.get(cfg.get("model_env", "AGENT_MODEL")) or cfg.get(
            "default_model", "gpt-4.1"
        )
        self.temperature = float(cfg.get("determinism", {}).get("temperature", 0))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


FEATURES = ["x1", "x2", "x3", "x4", "x5"]
TARGET = "y1"

"""配置加载：读取 config.yaml，缺失时回退环境变量。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 项目根 = 本文件向上三级（src/loopwc/config.py -> 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def data_dir(self) -> Path:
        rel = self.get("paths", "data_dir", default="data/matches")
        p = Path(rel)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def anthropic_api_key(self) -> str | None:
        return (
            self.get("llm", "api_key")
            or self.get("edit", "api_key")
            or os.environ.get("ANTHROPIC_API_KEY")
        )


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}
    return Config(raw=raw)

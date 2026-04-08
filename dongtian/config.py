"""Configuration loading for Dongtian."""

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "db_path": "~/.dongtian/palace.db",
    "embedding_api_key": None,
    "embedding_base_url": "https://api.siliconflow.cn/v1",
    "embedding_model": "BAAI/bge-m3",
    "embedding_dim": 1024,
    "chunk_size_max": 1500,
    "chunk_size_min": 200,
}

CONFIG_PATH = Path("~/.dongtian/config.json").expanduser()


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config.update(user_config)
    # env overrides
    if key := os.environ.get("SILICONFLOW_API_KEY"):
        config["embedding_api_key"] = key
    if key := os.environ.get("EMBEDDING_API_KEY"):
        config["embedding_api_key"] = key
    config["db_path"] = str(Path(config["db_path"]).expanduser())
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

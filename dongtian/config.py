"""Configuration loading for Dongtian."""

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "db_path": "~/.dongtian/cavern.db",
    "embedding_api_key": None,
    "embedding_base_url": "https://api.siliconflow.cn/v1",
    "embedding_model": "BAAI/bge-m3",
    "embedding_dim": 1024,
    "chunk_size_max": 1500,
    "chunk_size_min": 200,
    # Hook candidate auto-update pipeline
    "hook_candidate_auto_update": True,
    "hook_candidate_auto_ingest": True,
    "hook_candidate_since_days": 14,
    "hook_candidate_output_dir": "~/.dongtian/hook_candidates",
    "hook_candidate_hook_file": "~/.openharness-w8/hooks/strategy_route_env_guard.py",
    "hook_candidate_layer": "dongtian-system",
    "hook_candidate_chamber_prefix": "hook_candidates",
    "hook_candidate_promote_hit_threshold": 3,
    "hook_candidate_promote_session_threshold": 2,
    "hook_candidate_max_command_examples": 3,
    "hook_candidate_session_roots": [],
}

CONFIG_PATH = Path("~/.dongtian/config.json").expanduser()
_ALT_CONFIG_PATH = Path("~/.mempalace/config.json").expanduser()


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    cfg_path = CONFIG_PATH if CONFIG_PATH.exists() else _ALT_CONFIG_PATH
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config.update(user_config)
    # env overrides
    if key := os.environ.get("SILICONFLOW_API_KEY"):
        config["embedding_api_key"] = key
    if key := os.environ.get("EMBEDDING_API_KEY"):
        config["embedding_api_key"] = key
    config["db_path"] = str(Path(config["db_path"]).expanduser())
    config["hook_candidate_output_dir"] = str(Path(config["hook_candidate_output_dir"]).expanduser())
    config["hook_candidate_hook_file"] = str(Path(config["hook_candidate_hook_file"]).expanduser())
    roots = config.get("hook_candidate_session_roots") or []
    config["hook_candidate_session_roots"] = [str(Path(r).expanduser()) for r in roots]
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

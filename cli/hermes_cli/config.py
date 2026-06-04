"""Loads CLI config from env vars or ~/.hermes/config."""
import os
import json
from pathlib import Path


_CONFIG_PATH = Path.home() / ".hermes" / "config.json"


def load() -> dict:
    cfg = {}
    if _CONFIG_PATH.exists():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text())
        except Exception:
            pass
    cfg["url"] = os.environ.get("HERMES_URL", cfg.get("url", "http://localhost:8000"))
    cfg["api_key"] = os.environ.get("HERMES_API_KEY", cfg.get("api_key", ""))
    return cfg


def save(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

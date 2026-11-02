"""Catálogo de autosserviço (fonte única dos itens e canais)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "self_service_catalog.json"


@lru_cache
def load_catalog() -> dict[str, dict]:
    items = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {item["id"]: item for item in items}


def catalog_names() -> str:
    return ", ".join(item["name"].lower() for item in load_catalog().values())


def catalog_for_prompt() -> str:
    return "\n".join(f"- {i}: {it['name']} — {it['description']}" for i, it in load_catalog().items())

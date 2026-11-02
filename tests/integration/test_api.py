"""Contrato HTTP do endpoint (requer fastapi e httpx instalados)."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_chat_contract_and_background_evaluation():
    resp = client.post("/chat", json={"message": "Quero pedir bobinas", "user_id": "cliente1988"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"response", "route", "sources", "tools_used", "conversation_closed", "trace_id"}
    assert body["route"] == {"triage": "adquirencia", "agents": ["self_service"], "theme": "bobinas"}
    # o TestClient executa as background tasks: a avaliação já foi gravada
    assert client.get("/metrics").json()["dna_getnet"]["avaliacoes"] == 1


def test_invalid_payload_returns_422():
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_health():
    assert client.get("/health").json() == {"status": "ok"}

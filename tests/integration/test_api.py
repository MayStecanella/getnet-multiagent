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
    assert {"response", "route", "sources", "tools_used", "conversation_closed", "trace_id",
            "conversation_id", "status"} <= set(body)
    assert body["route"] == {"triage": "adquirencia", "agents": ["self_service"], "theme": "bobinas"}
    # o TestClient executa as background tasks: a avaliação já foi gravada
    assert client.get("/metrics").json()["dna_getnet"]["avaliacoes"] == 1


def test_invalid_payload_returns_422():
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_health():
    body = client.get("/health").json()
    assert body["status"] in ("ok", "incompleto") and body["modo"] in ("real", "simulado")


def test_screens_are_served():
    for path in ("/", "/cliente", "/especialista", "/qualidade", "/static/app.css",
                 "/static/exemplos/replay.csv"):
        assert client.get(path).status_code == 200, path


def test_access_code_protects_api(monkeypatch):
    from dataclasses import replace

    from app.web import handlers
    monkeypatch.setattr(handlers, "settings", replace(handlers.settings, access_code="segredo"))
    payload = {"message": "Quero pedir bobinas", "user_id": "cliente1988"}
    assert client.post("/chat", json=payload).status_code == 401
    assert client.get("/api/quality/metrics").status_code == 401
    assert client.get("/api/config").json()["access_code_required"] is True
    assert client.get("/health").status_code == 200
    ok = client.post("/chat", json=payload, headers={"X-Access-Code": "segredo"})
    assert ok.status_code == 200


def test_handoff_and_specialist_over_http():
    first = client.post("/chat", json={"message": "Quero pedir bobinas", "user_id": "c1", "channel": "web"}).json()
    assert first["awaiting_feedback"] is True
    cid = first["conversation_id"]
    body = client.post("/chat", json={"message": "Não", "user_id": "c1", "conversation_id": cid,
                                      "channel": "web"}).json()
    assert body["status"] == "aguardando_humano"
    assert client.get("/api/agent/queue").json()["queue"][0]["id"] == cid
    assert client.post(f"/api/agent/conversations/{cid}/claim", json={"agent_name": "Ana"}).status_code == 200
    sent = client.post(f"/api/agent/conversations/{cid}/messages",
                       json={"agent_name": "Ana", "text": "Olá! Próximo passo: acesse o portal."})
    assert sent.status_code == 200
    msgs = client.get(f"/api/conversations/{cid}/messages", params={"user_id": "c1"}).json()["messages"]
    assert msgs[-1]["author"] == "humano"
    assert client.get("/api/quality/metrics").json()["dna_getnet"]["humano"]["avaliacoes"] == 1
    conflict = client.post(f"/api/agent/conversations/{cid}/claim", json={"agent_name": "Bruno"})
    assert conflict.status_code == 409


def test_batch_over_http():
    started = client.post("/api/quality/batch", json={"csv": "mensagem\nQuero pedir bobinas\n", "name": "t"}).json()
    assert client.get(f"/api/quality/batch/{started['id']}").json()["status"] == "concluido"
    assert client.post("/api/quality/batch", json={"csv": "x\n1\n"}).status_code == 422

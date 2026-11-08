"""Regras dos endpoints (sem servidor HTTP): acesso, limites, especialista e qualidade."""
from dataclasses import replace

import pytest

from app.web import handlers
from app.web.handlers import ApiError


def run_tasks(out):
    body, tasks = out
    for fn, args, kwargs in tasks:
        fn(*args, **kwargs)
    return body


def test_access_code(monkeypatch):
    monkeypatch.setattr(handlers, "settings", replace(handlers.settings, access_code="getnet2026"))
    with pytest.raises(ApiError) as exc:
        handlers.check_access("errado")
    assert exc.value.status == 401
    handlers.check_access("getnet2026")
    assert handlers.public_config()["access_code_required"] is True


def test_rate_limit_per_ip(monkeypatch):
    monkeypatch.setattr(handlers, "settings", replace(handlers.settings, rate_limit_per_minute=2))
    payload = {"message": "Quero pedir bobinas", "user_id": "cliente1988"}
    handlers.chat(payload, "1.1.1.1")
    handlers.chat(payload, "1.1.1.1")
    with pytest.raises(ApiError) as exc:
        handlers.chat(payload, "1.1.1.1")
    assert exc.value.status == 429
    handlers.chat(payload, "2.2.2.2")  # outro IP segue livre


def test_chat_validation():
    with pytest.raises(ApiError) as exc:
        handlers.chat({"message": "", "user_id": "x"}, "ip")
    assert exc.value.status == 422


def test_chat_schedules_quality_evaluation():
    body = run_tasks(handlers.chat({"message": "Quero pedir bobinas", "user_id": "cliente1988"}, "ip"))
    evals = handlers.quality_evaluations("ia")["evaluations"]
    assert len(evals) == 1 and evals[0]["conversation_id"] == body["conversation_id"]


def test_full_specialist_flow_and_human_quality():
    first = run_tasks(handlers.chat({"message": "Quando cai o dinheiro das vendas de ontem?",
                                     "user_id": "cliente1988", "channel": "web"}, "ip"))
    assert first["awaiting_feedback"] is True
    cid = first["conversation_id"]
    assert handlers.client_messages(cid, "cliente1988")["conversation"]["awaiting_feedback"] is True
    body = run_tasks(handlers.chat({"message": "Não", "user_id": "cliente1988", "conversation_id": cid,
                                    "channel": "web"}, "ip"))
    assert body["status"] == "aguardando_humano" and body["feedback"] == "nao"
    queue = handlers.agent_queue()["queue"]
    assert queue[0]["id"] == cid and queue[0]["reason"] == "nao_resolvido"

    with pytest.raises(ApiError) as exc:  # responder sem assumir
        handlers.agent_send(cid, {"agent_name": "Ana", "text": "Olá"})
    assert exc.value.status == 409

    handlers.agent_claim(cid, {"agent_name": "Ana"})
    run_tasks(handlers.agent_send(cid, {"agent_name": "Ana",
                                        "text": "Olá! Sou a Ana. Próximo passo: acesse o portal Minha Conta."}))
    run_tasks(handlers.agent_send(cid, {"agent_name": "Ana", "text": "ok"}))

    # o cliente vê a resposta do especialista
    msgs = handlers.client_messages(cid, "cliente1988")["messages"]
    assert [m["author"] for m in msgs][-3:] == ["sistema", "humano", "humano"]
    with pytest.raises(ApiError):
        handlers.client_messages(cid, "outro_usuario")

    # a qualidade avalia o especialista com a mesma régua de DNA
    detail = handlers.agent_conversation(cid)
    scores = sorted(e["total"] for e in detail["evaluations"])
    assert len(scores) == 2 and scores[0] < scores[1]  # "ok" é pior que a resposta completa
    metrics = handlers.quality_metrics()
    assert metrics["dna_getnet"]["humano"]["avaliacoes"] == 2
    assert metrics["dna_getnet"]["por_especialista"][0]["especialista"] == "Ana"
    assert metrics["transbordo"]["taxa_transbordo"] == 100.0

    handlers.agent_close(cid, {"agent_name": "Ana"})
    assert handlers.agent_queue()["queue"] == []


def test_specialist_text_is_masked():
    body, _ = handlers.chat({"message": "Quero pedir bobinas", "user_id": "c"}, "ip")
    cid = body["conversation_id"]
    handlers.chat({"message": "não resolveu", "user_id": "c", "conversation_id": cid}, "ip")
    handlers.agent_claim(cid, {"agent_name": "Ana"})
    handlers.agent_send(cid, {"agent_name": "Ana", "text": "Confirme o CPF 123.456.789-09"})
    last = handlers.client_messages(cid, "c")["messages"][-1]
    assert "123.456" not in last["text"]


def test_batch_endpoint_runs_and_reports():
    body = run_tasks(handlers.batch_start({"csv": "mensagem,resposta\nOi,Olá! Acesse o portal.\n",
                                           "name": "teste"}, "ip"))
    batch = handlers.batch_get(body["id"])
    assert batch["status"] == "concluido" and batch["summary"]["avaliadas"] == 1
    assert handlers.batch_list()["batches"][0]["id"] == body["id"]
    with pytest.raises(ApiError) as exc:
        handlers.batch_start({"csv": "coluna\nx\n"}, "ip")
    assert exc.value.status == 422

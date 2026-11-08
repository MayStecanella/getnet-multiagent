"""Testes off-line: o sistema inteiro funciona sem internet.

O fixture bloqueia qualquer conexão de rede. Se algum componente tentar sair para a internet
(LLM, embeddings, crawl, busca web), o teste falha.
"""
import socket

import pytest

from app import llm
from app.offline import FakeLLM, FakeRetriever
from app.rag import retriever
from app.web import handlers


@pytest.fixture
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("tentativa de acesso à rede durante teste off-line")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def test_offline_mode_uses_simulated_components():
    # o conftest liga OFFLINE_MODE; sem injeção manual, as fábricas devolvem os simulados
    llm._agent_llm = llm._judge_llm = None
    retriever._retriever = None
    assert isinstance(llm.get_llm(), FakeLLM)
    assert isinstance(llm.get_judge_llm(), FakeLLM)
    assert isinstance(retriever.get_retriever(), FakeRetriever)


def test_full_journey_without_network(no_network):
    llm._agent_llm = llm._judge_llm = None
    retriever._retriever = None

    def run(out):
        body, tasks = out
        for fn, args, kwargs in tasks:
            fn(*args, **kwargs)
        return body

    # cliente: produto, conta, web, autosserviço e transbordo
    for msg in ["What's the difference between the Get Clássica and the Get Smart?",
                "When will the money from yesterday's sales be deposited?",
                "What's the euro exchange rate today?",
                "Quero pedir bobinas"]:
        body = run(handlers.chat({"message": msg, "user_id": "cliente1988"}, "ip"))
        assert body["response"]
    cid = body["conversation_id"]  # conversa das bobinas
    body = run(handlers.chat({"message": "Não", "user_id": "cliente1988", "conversation_id": cid}, "ip"))
    assert body["status"] == "aguardando_humano"

    # especialista assume e responde
    handlers.agent_claim(cid, {"agent_name": "Ana"})
    run(handlers.agent_send(cid, {"agent_name": "Ana", "text": "Olá! Próximo passo: acesse o portal."}))

    # qualidade: métricas e lote
    m = handlers.quality_metrics()
    assert m["atendimentos"] == 4 and m["dna_getnet"]["humano"]["avaliacoes"] == 1
    assert m["resolucao"]["nao_resolvidas"] == 1
    batch = run(handlers.batch_start({"csv": "mensagem\nQuero pedir bobinas\n"}, "ip"))
    assert handlers.batch_get(batch["id"])["status"] == "concluido"

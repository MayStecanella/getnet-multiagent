"""Integração: os cenários percorrem o grafo completo (LLM e RAG simulados)."""
import pytest

from app.observability.store import get_interaction, metrics
from app.service import handle_message
from app.tools.web_search import SearchResponse, SearchResult
from tests.scenarios import SCENARIOS, agents_match


@pytest.fixture(autouse=True)
def fake_web(monkeypatch):
    monkeypatch.setattr("app.agents.web.web_search",
                        lambda q, **kw: SearchResponse([SearchResult("Resultado", "https://exemplo.com/cotacao", "conteúdo")]))


@pytest.mark.parametrize("message,triage,agents,tools", SCENARIOS)
def test_scenario_routes(message, triage, agents, tools):
    r = handle_message(message, "cliente1988")
    assert not r.error
    assert r.triage == triage
    assert agents_match(r.agents, agents)
    for t in tools:
        assert t in r.tools_used
    assert r.response
    assert r.conversation_closed is (triage not in ("adquirencia", "humano"))
    assert get_interaction(r.trace_id) is not None


def test_combined_route_greets_once():
    r = handle_message("How does receivables advance (antecipação) work with Getnet?", "cliente1988")
    assert r.response.count("Olá") == 1
    assert "Minha Conta" in r.response or "minhaconta" in r.response


def test_sensitive_data_never_reaches_the_store():
    r = handle_message("Meu CPF é 123.456.789-09", "cliente1988")
    assert "123.456" not in get_interaction(r.trace_id)["message_masked"]


def test_recontact_flows_through_service():
    first = handle_message("When will the money from yesterday's sales be deposited?", "cliente1988")
    handle_message("Quando cai o dinheiro? Ainda não recebi", "cliente1988")
    assert get_interaction(first.trace_id)["recontacted"] == 1
    assert metrics()["assertividade"]["recontatos"] == 1


def test_graph_failure_returns_cordial_fallback(monkeypatch):
    def boom(*_):
        raise RuntimeError("LLM fora")
    from app import llm
    monkeypatch.setattr(llm.get_llm(), "structured", boom)
    r = handle_message("Qual a diferença entre a Get Smart e a Get Clássica?", "cliente1988")
    assert r.error and "Get Ajuda" in r.response


def test_web_without_api_key_answers_gracefully(monkeypatch):
    monkeypatch.setattr("app.agents.web.web_search", lambda q, **kw: SearchResponse([]))  # busca sem resultado
    r = handle_message("Qual a cotação do dólar hoje?", "cliente1988")
    assert r.triage == "web" and r.conversation_closed and "até logo" in r.response.lower()

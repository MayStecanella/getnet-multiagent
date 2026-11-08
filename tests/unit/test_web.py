"""Busca na web para perguntas atuais (placar, classificação) e gerais."""
from dataclasses import replace
from datetime import datetime

import pytest

from app import prompts
from app.agents import web
from app.config import settings
from app.service import handle_message
from app.tools import web_search as ws
from app.tools.web_search import SearchResponse, SearchResult

CURRENT = [
    "Qual o placar do último jogo do Corinthians?",
    "Como está a classificação do Brasileirão?",
    "Quem ganhou a Fórmula 1 no domingo?",
    "Qual a cotação do dólar hoje?",
]


@pytest.mark.parametrize("text", CURRENT)
def test_current_questions_go_to_web_and_never_to_specialist(text):
    r = handle_message(text, "cliente1988", channel="web")
    assert r.triage == "web" and r.agents == ["web_search"]
    assert r.conversation_closed and not r.awaiting_feedback


def test_opinion_about_football_is_out_of_scope():
    r = handle_message("Qual time você acha que vai ganhar o Brasileirão?", "cliente1988")
    assert r.triage == "fora_de_escopo"


def test_triage_prompt_lists_sports_as_web():
    assert "placar" in prompts.GUARDRAIL_TOPIC and "classificação do Brasileirão" in prompts.GUARDRAIL_TOPIC


def test_query_plan_carries_today(monkeypatch):
    seen = {}

    class Spy:
        def structured(self, system, user, schema):
            seen["system"] = system
            return schema(kind="resultado", query="Corinthians placar último jogo novembro 2026", topic="news", recency="week")

    monkeypatch.setattr(web, "get_llm", lambda: Spy())
    plan = web.plan_query("placar do último jogo do Corinthians", datetime(2026, 11, 8, 9, 0, tzinfo=web.BRT))
    assert "08/11/2026" in seen["system"] and "novembro 2026" in seen["system"]
    assert plan.topic == "news" and plan.recency == "week"


def test_query_plan_falls_back_to_question(monkeypatch):
    class Broken:
        def structured(self, *a):
            raise RuntimeError("fora")

    monkeypatch.setattr(web, "get_llm", lambda: Broken())
    assert web.plan_query("placar do jogo", web.now_brt()).query == "placar do jogo"


def test_payload_news_with_time_window():
    p = ws.build_payload("Brasileirão classificação", "news", "week")
    assert p["topic"] == "news" and p["time_range"] == "week" and p["include_answer"] is True
    assert "time_range" not in ws.build_payload("capital da Austrália", "general", "any")


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(ws, "settings", replace(settings, offline_mode=False, tavily_api_key="tvly-teste"))


def test_empty_window_retries_without_window(online, monkeypatch):
    calls = []

    def fake_post(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {"results": []}
        return {"results": [{"title": "t", "url": "https://ge.globo.com/x", "content": "c"}]}

    monkeypatch.setattr(ws, "_post", fake_post)
    resp = ws.web_search("Corinthians placar", "news", "week")
    assert len(calls) == 2 and "time_range" not in calls[1] and resp.results[0].url == "https://ge.globo.com/x"


def test_results_sorted_newest_first(online, monkeypatch):
    monkeypatch.setattr(ws, "_post", lambda p: {"answer": "resumo", "results": [
        {"title": "antigo", "url": "a", "content": "", "published_date": "2026-10-01"},
        {"title": "sem data", "url": "b", "content": ""},
        {"title": "novo", "url": "c", "content": "", "published_date": "2026-11-07"},
    ]})
    resp = ws.web_search("x", "news", "week")
    assert [r.title for r in resp.results] == ["novo", "antigo", "sem data"] and resp.answer == "resumo"


def test_answer_gets_dates_and_sources(monkeypatch):
    seen = {}
    resp = SearchResponse([SearchResult("Jogo", "https://ge.globo.com/jogo", "Corinthians 2 x 1", "2026-11-07")], "resumo")
    monkeypatch.setattr(web, "web_search", lambda q, **kw: resp)

    class Spy:
        def structured(self, system, user, schema):
            return schema(query=user, topic="news", recency="week")

        def text(self, system, user):
            seen["system"], seen["user"] = system, user
            return "Olá! Corinthians 2 x 1. Fonte: ge.globo.com. Obrigado e até logo!"

    monkeypatch.setattr(web, "get_llm", lambda: Spy())
    out = web.web_node({"masked_message": "placar do último jogo do Corinthians"})
    assert 'data="2026-11-07"' in seen["user"] and "resumo do buscador" in seen["user"]
    assert "mais recente" in seen["system"]
    assert out["outputs"][0].sources == ["https://ge.globo.com/jogo"] and out["conversation_closed"]


def test_no_results_answers_gracefully(monkeypatch):
    monkeypatch.setattr(web, "web_search", lambda q, **kw: SearchResponse([]))
    out = web.web_node({"masked_message": "placar"})
    assert "até logo" in out["response"].lower() and out["conversation_closed"]


def test_health_tells_real_from_simulated(monkeypatch):
    from app.web import handlers

    monkeypatch.setattr(handlers, "settings", replace(settings, offline_mode=True))
    assert handlers.health()["modo"] == "simulado"
    monkeypatch.setattr(handlers, "settings", replace(settings, offline_mode=False, tavily_api_key=""))
    h = handlers.health()
    assert h["modo"] == "real" and h["checks"]["busca_web"] is False and h["status"] == "incompleto"


def test_next_game_is_agenda_keeps_relevance_order_and_rules(monkeypatch):
    seen = {}

    def fake_search(q, **kw):
        seen["kw"] = kw
        return SearchResponse([SearchResult("Agenda", "https://lance.com.br/agenda", "18/11 x Grêmio; 28/11 final")])

    class Spy:
        def structured(self, system, user, schema):
            return schema(kind="agenda", query="Palmeiras próximos jogos calendário novembro 2026",
                          topic="general", recency="any")

        def text(self, system, user):
            seen["system"] = system
            return "Olá! O próximo jogo é em 18/11. Fonte: lance.com.br. Até logo!"

    monkeypatch.setattr(web, "web_search", fake_search)
    monkeypatch.setattr(web, "get_llm", lambda: Spy())
    web.web_node({"masked_message": "Quando é o próximo jogo do Palmeiras?"})
    assert seen["kw"]["newest_first"] is False and seen["kw"]["recency"] == "any"
    assert "agenda" in seen["system"] and "MAIS PRÓXIMA" in seen["system"] and "comentado" in seen["system"]


def test_agenda_search_does_not_reorder_by_publication(online, monkeypatch):
    monkeypatch.setattr(ws, "_post", lambda p: {"results": [
        {"title": "calendário", "url": "a", "content": "", "published_date": ""},
        {"title": "notícia da final", "url": "b", "content": "", "published_date": "2026-11-07"},
    ]})
    assert [r.title for r in ws.web_search("x", newest_first=False).results] == ["calendário", "notícia da final"]


def test_offline_plan_detects_agenda():
    plan = web.plan_query("Quando é o próximo jogo do Palmeiras?", web.now_brt())
    assert plan.kind == "agenda" and plan.recency == "any"

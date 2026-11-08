"""Tool de busca na web, compartilhada (acionada na triagem para perguntas gerais e atuais)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings

log = logging.getLogger("web_search")

TOPICS = ("general", "news")
RECENCY = ("day", "week", "month", "any")
RAW_PAGES = 3      # páginas que entram com o conteúdo completo (truncado)
RAW_CHARS = 3500


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    published: str = ""  # data de publicação (só vem nas buscas de notícias)


@dataclass
class SearchResponse:
    results: list[SearchResult]
    answer: str = ""  # resumo do próprio buscador, usado só como apoio


def build_payload(query: str, topic: str = "general", recency: str = "any", max_results: int = 6) -> dict:
    payload = {
        "query": query,
        "topic": topic if topic in TOPICS else "general",
        "search_depth": "advanced",
        "include_answer": True,
        "include_raw_content": True,
        "max_results": max_results,
    }
    if recency in ("day", "week", "month"):
        payload["time_range"] = recency
    return payload


def _post(payload: dict) -> dict | None:
    import httpx

    try:
        resp = httpx.post("https://api.tavily.com/search", json={**payload, "api_key": settings.tavily_api_key}, timeout=25,
                          headers={"Authorization": f"Bearer {settings.tavily_api_key}"})
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        log.warning("busca web falhou: %s", exc)
        return None


def web_search(query: str, topic: str = "general", recency: str = "any", max_results: int = 6) -> SearchResponse:
    """Busca via Tavily. Sem chave configurada ou em erro, devolve resposta vazia.

    Para assuntos atuais (topic="news" com janela de tempo), se a janela não trouxer nada,
    repete a busca sem a janela para não deixar o cliente sem resposta.
    """
    if settings.offline_mode:
        from app.offline import fake_web_results

        return SearchResponse(fake_web_results(query))
    if not settings.tavily_api_key:
        return SearchResponse([])
    data = _post(build_payload(query, topic, recency, max_results))
    if data is not None and not data.get("results") and (recency != "any" or topic != "general"):
        data = _post(build_payload(query, "general", "any", max_results))
    if not data:
        return SearchResponse([])
    results = []
    for i, r in enumerate(data.get("results", [])):
        content = r.get("content", "") or ""
        raw = (r.get("raw_content") or "").strip()
        if raw and i < RAW_PAGES:  # tabelas e placares completos das páginas mais relevantes
            content = f"{content}\n\nTrecho da página:\n{raw[:RAW_CHARS]}"
        results.append(SearchResult(r.get("title", ""), r.get("url", ""), content, r.get("published_date", "") or ""))
    # mais recentes primeiro (os sem data ficam no fim, na ordem de relevância)
    results.sort(key=lambda r: r.published or "", reverse=True)
    return SearchResponse(results, data.get("answer") or "")

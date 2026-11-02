"""Tool de busca na web, compartilhada (acionada na triagem para perguntas gerais)."""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass
class SearchResult:
    title: str
    url: str
    content: str


def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Busca via Tavily. Sem chave configurada ou em erro, devolve lista vazia."""
    if not settings.tavily_api_key:
        return []
    import httpx

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.tavily_api_key, "query": query, "max_results": max_results},
            timeout=20,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return []
    return [
        SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""))
        for r in resp.json().get("results", [])
    ]

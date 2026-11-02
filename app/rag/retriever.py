"""Recuperação online: busca vetorial no Chroma com várias queries e deduplicação."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Protocol

from app.config import settings

COLLECTION = "getnet_kb"


@dataclass
class Chunk:
    text: str
    url: str
    title: str


class Retriever(Protocol):
    def search(self, queries: list[str], k: int) -> list[Chunk]: ...


_vectorstore = None


def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        from langchain.embeddings import init_embeddings
        from langchain_chroma import Chroma

        _vectorstore = Chroma(
            collection_name=COLLECTION,
            embedding_function=init_embeddings(settings.embedding_model, provider=settings.embedding_provider),
            persist_directory=str(settings.chroma_dir),
        )
    return _vectorstore


def reset_vectorstore() -> None:
    global _vectorstore
    _vectorstore = None
    shutil.rmtree(settings.chroma_dir, ignore_errors=True)


MAX_CHUNKS_PER_URL = 2
MAX_CHUNKS_TOTAL = 10


class ChromaRetriever:
    """Busca com MMR (relevância + diversidade) para cada query, deduplicada.

    O limite por URL evita que uma única página ocupe todo o contexto, o que importa em
    perguntas que comparam produtos.
    """

    def search(self, queries: list[str], k: int) -> list[Chunk]:
        store = get_vectorstore()
        seen: set[tuple] = set()
        per_url: dict[str, int] = {}
        out: list[Chunk] = []
        for q in queries:
            for doc in store.max_marginal_relevance_search(q, k=k, fetch_k=k * 5):
                url = doc.metadata.get("url", "")
                key = (url, doc.page_content[:120])
                if key in seen or per_url.get(url, 0) >= MAX_CHUNKS_PER_URL:
                    continue
                seen.add(key)
                per_url[url] = per_url.get(url, 0) + 1
                out.append(Chunk(doc.page_content, url, doc.metadata.get("title", "")))
                if len(out) >= MAX_CHUNKS_TOTAL:
                    return out
        return out


_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = ChromaRetriever()
    return _retriever


def set_retriever(r: Retriever) -> None:
    """Injeção usada pelos testes."""
    global _retriever
    _retriever = r

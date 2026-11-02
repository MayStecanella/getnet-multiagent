"""Ingestão offline do RAG: crawl -> limpeza -> chunks -> embeddings -> Chroma.

Uso: python -m app.rag.ingest [--force]
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from app.config import settings

log = logging.getLogger("ingest")

USER_AGENT = "getnet-multiagent-challenge/1.0 (+ingestao RAG)"
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".webp", ".ico", ".css", ".js")


@dataclass
class Page:
    url: str
    title: str
    text: str
    lang: str


def clean_html(html: str) -> tuple[str, str]:
    """Remove menus, rodapés e scripts. Devolve (título, texto)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"]):
        tag.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    main = soup.find("main") or soup.body or soup
    lines = [ln.strip() for ln in main.get_text("\n").splitlines()]
    text = "\n".join(ln for ln in lines if len(ln) > 2)
    return title, text


def normalize_url(url: str) -> str:
    """Remove fragmento e query (ex.: ?modal_open=154), que só duplicam páginas."""
    u = urlparse(urldefrag(url)[0])
    return u._replace(query="", params="").geturl()


def _in_scope(url: str, root: str) -> bool:
    u, r = urlparse(url), urlparse(root)
    path = u.path.lower()
    return (
        u.netloc == r.netloc
        and u.path.startswith(r.path.rstrip("/"))
        and not path.endswith(SKIP_EXT)
        and not any(p and p in path for p in settings.rag_exclude)
    )


def crawl(root: str, max_pages: int) -> list[Page]:
    """BFS dentro do mesmo domínio e prefixo de caminho, respeitando robots.txt."""
    import httpx
    from bs4 import BeautifulSoup

    robots = RobotFileParser()
    robots.set_url(urljoin(root, "/robots.txt"))
    try:
        robots.read()
    except Exception:  # robots inacessível: segue com cautela
        robots = None

    lang = "en" if "/en" in urlparse(root).path else "pt"
    seen, queue, pages = {root}, deque([root]), []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20) as client:
        while queue and len(pages) < max_pages:
            url = queue.popleft()
            if robots and not robots.can_fetch(USER_AGENT, url):
                continue
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                log.warning("falha em %s: %s", url, exc)
                continue
            if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                continue
            title, text = clean_html(resp.text)
            if len(text) > 200:
                pages.append(Page(url=url, title=title, text=text, lang=lang))
                log.info("[%d] %s", len(pages), url)
            for a in BeautifulSoup(resp.text, "html.parser").find_all("a", href=True):
                nxt = normalize_url(urljoin(url, a["href"]))
                if nxt not in seen and _in_scope(nxt, root):
                    seen.add(nxt)
                    queue.append(nxt)
            time.sleep(0.3)  # gentileza com o servidor
    return pages


def chunk_pages(pages: list[Page]):
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
    )
    collected_at = datetime.now(timezone.utc).isoformat()
    docs = [
        Document(page_content=p.text, metadata={"url": p.url, "title": p.title, "lang": p.lang, "collected_at": collected_at})
        for p in pages
    ]
    chunks = splitter.split_documents(docs)
    for c in chunks:  # o título no chunk melhora a recuperação
        c.page_content = f"{c.metadata['title']}\n{c.page_content}"
    return chunks


def index_exists() -> bool:
    return settings.chroma_dir.exists() and any(settings.chroma_dir.iterdir())


def ingest(force: bool = False) -> int:
    if index_exists() and not force:
        log.info("Índice já existe em %s (use --force para recriar)", settings.chroma_dir)
        return 0
    from app.rag.retriever import get_vectorstore, reset_vectorstore

    if force:
        reset_vectorstore()
    pages: list[Page] = []
    seen_urls: set[str] = set()
    for root in settings.rag_sources:
        for page in crawl(root.strip(), settings.max_pages_per_source):
            if page.url not in seen_urls:  # fontes do mesmo domínio podem se sobrepor
                seen_urls.add(page.url)
                pages.append(page)
        log.info("Fonte %s: total acumulado de %d páginas", root.strip(), len(pages))
    chunks = chunk_pages(pages)
    if chunks:
        get_vectorstore().add_documents(chunks)
    log.info("Ingestão concluída: %d páginas, %d chunks", len(pages), len(chunks))
    return len(chunks)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="recria o índice")
    ingest(force=parser.parse_args().force)

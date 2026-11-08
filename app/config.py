"""Configuração central, lida de variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    """Variável vazia no .env (ex.: RAG_SOURCES=) usa o padrão."""
    return os.getenv(name) or default


@dataclass(frozen=True)
class Settings:
    # LLM de geração (agentes) e de avaliação (qualidade): provedores/modelos independentes
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    judge_provider: str = field(default_factory=lambda: _env("JUDGE_PROVIDER", _env("LLM_PROVIDER", "openai")))
    judge_model: str = field(default_factory=lambda: _env("JUDGE_MODEL", "gpt-4o"))

    # RAG
    embedding_provider: str = field(default_factory=lambda: _env("EMBEDDING_PROVIDER", "openai"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "text-embedding-3-small"))
    chunk_size: int = field(default_factory=lambda: int(_env("CHUNK_SIZE", "800")))
    chunk_overlap: int = field(default_factory=lambda: int(_env("CHUNK_OVERLAP", "100")))
    top_k: int = field(default_factory=lambda: int(_env("TOP_K", "4")))
    max_pages_per_source: int = field(default_factory=lambda: int(_env("MAX_PAGES_PER_SOURCE", "150")))
    rag_sources: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            _env(
                "RAG_SOURCES",
                # Get Ajuda: o hub /get-ajuda/ aponta para as FAQs em /duvidas/ e para as
                # páginas de produto; por isso todas entram como fontes.
                "https://site.getnet.com.br/get-ajuda/,"
                "https://site.getnet.com.br/duvidas/,"
                "https://site.getnet.com.br/todas-as-maquininhas/,"
                "https://site.getnet.com.br/pix/,"
                "https://site.getnet.com.br/crediario/,"
                "https://site.getnet.com.br/link-de-pagamento/,"
                "https://site.getnet.com.br/get-tap/,"
                "https://site.getnet.com.br/conta-digital/,"
                "https://site.getnet.com.br/taxas-personalizadas/,"
                "https://www.getnet.net/en/",
            ).split(",")
        )
    )

    # Trechos de caminho ignorados no crawl (blog e carreiras trazem ruído para o suporte)
    rag_exclude: tuple[str, ...] = field(
        default_factory=lambda: tuple(_env("RAG_EXCLUDE", "/blog/,/carreira,/careers").split(","))
    )

    # Web search (Tavily). Sem chave, a busca web responde que está indisponível.
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY", ""))

    # Persistência
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./var")))

    # Qualidade
    recontact_window_hours: int = field(default_factory=lambda: int(_env("RECONTACT_WINDOW_HOURS", "24")))
    batch_max_rows: int = field(default_factory=lambda: int(_env("BATCH_MAX_ROWS", "100")))

    # Transbordo: na N-ésima pergunta sobre o mesmo tema na conversa, vai para o especialista
    handoff_after_attempts: int = field(default_factory=lambda: int(_env("HANDOFF_AFTER_ATTEMPTS", "4")))

    # Modo off-line: LLM, RAG e busca web simulados (sem chave e sem internet)
    offline_mode: bool = field(default_factory=lambda: _env("OFFLINE_MODE", "false").lower() == "true")

    # Proteção da versão publicada: código de acesso (vazio = sem proteção) e limites de uso
    access_code: str = field(default_factory=lambda: _env("ACCESS_CODE", ""))
    rate_limit_per_minute: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "12")))
    daily_message_cap: int = field(default_factory=lambda: int(_env("DAILY_MESSAGE_CAP", "500")))
    chroma_dir_override: str = field(default_factory=lambda: _env("CHROMA_DIR", ""))

    # Canais de autosserviço
    portal_url: str = field(default_factory=lambda: _env("PORTAL_URL", "https://minhaconta.getnet.com.br/login"))
    app_download_hint: str = field(
        default_factory=lambda: _env(
            "APP_DOWNLOAD_HINT",
            'o QR Code para baixar o app, em https://site.getnet.com.br na aba "Sou cliente"',
        )
    )

    @property
    def chroma_dir(self) -> Path:
        return Path(self.chroma_dir_override) if self.chroma_dir_override else self.data_dir / "chroma"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "atendimento.db"


settings = Settings()

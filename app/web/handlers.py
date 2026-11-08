"""Regras dos endpoints, independentes do framework HTTP.

Cada função recebe dados simples e devolve um dicionário (ou um par (corpo, tarefas), em que
as tarefas rodam em segundo plano depois da resposta). O app/main.py só liga isso ao FastAPI.
Assim as regras ficam testáveis sem servidor.
"""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Any, Callable

from app.agents.guardrails import mask_sensitive
from app.agents.quality import evaluate_safely
from app.batch_eval import BatchError, parse_csv, run_batch
from app.config import settings
from app.observability import store
from app.service import handle_message

Task = tuple[Callable[..., Any], tuple, dict]


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- acesso e limites -----------------------------------------------------------
def check_access(code: str | None) -> None:
    if settings.access_code and not hmac.compare_digest((code or "").strip(), settings.access_code):
        raise ApiError(401, "código de acesso inválido")


class RateLimiter:
    """Limite por IP (mensagens por minuto) e teto diário global, para proteger os créditos do LLM."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)
        self._day = date.today()
        self._day_count = 0

    def hit(self, ip: str, cost: int = 1) -> None:
        now = time.monotonic()
        with self._lock:
            if date.today() != self._day:
                self._day, self._day_count = date.today(), 0
            if self._day_count + cost > settings.daily_message_cap:
                raise ApiError(429, "limite diário de uso atingido; tente novamente amanhã")
            hits = self._hits[ip]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= settings.rate_limit_per_minute:
                raise ApiError(429, "muitas mensagens em pouco tempo; aguarde um minuto")
            hits.append(now)
            self._day_count += cost

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._day_count = 0


limiter = RateLimiter()


def _text(payload: dict, key: str, max_len: int, required: bool = True) -> str:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ApiError(422, f'o campo "{key}" é obrigatório')
        return ""
    if not isinstance(value, str):
        raise ApiError(422, f'o campo "{key}" deve ser texto')
    value = value.strip()
    if len(value) > max_len:
        raise ApiError(422, f'o campo "{key}" passa de {max_len} caracteres')
    return value


def _seconds_since(iso: str | None) -> int | None:
    if not iso:
        return None
    return int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds())


# --- configuração ----------------------------------------------------------------
def health() -> dict:
    """Mostra se o atendimento está no funcionamento real (LLM, base e internet de verdade)."""
    import os

    if settings.offline_mode:
        return {"status": "ok", "modo": "simulado", "aviso": "OFFLINE_MODE=true: respostas simuladas, só para testes"}
    checks = {
        "llm": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")),
        "busca_web": bool(settings.tavily_api_key),
        "base_conhecimento": settings.chroma_dir.exists(),
    }
    return {"status": "ok" if all(checks.values()) else "incompleto", "modo": "real", "checks": checks}


def public_config() -> dict:
    return {"access_code_required": bool(settings.access_code), "offline_mode": settings.offline_mode}


# --- cliente --------------------------------------------------------------------
def chat(payload: dict, ip: str) -> tuple[dict, list[Task]]:
    message = _text(payload, "message", 2000)
    user_id = _text(payload, "user_id", 64)
    conversation_id = _text(payload, "conversation_id", 64, required=False) or None
    channel = "web" if payload.get("channel") == "web" else "api"
    limiter.hit(ip)
    r = handle_message(message, user_id, conversation_id, channel=channel)
    tasks: list[Task] = []
    if not r.error and not r.delivered_to_human and r.triage != "resolucao":
        # Qualidade assíncrona: roda depois que a resposta é enviada
        tasks.append((evaluate_safely, (r.trace_id, r.masked_message, r.response, r.sources),
                      {"conversation_id": r.conversation_id}))
    body = {
        "response": r.response,
        "route": {"triage": r.triage, "agents": r.agents, "theme": r.theme},
        "sources": r.sources,
        "tools_used": r.tools_used,
        "conversation_closed": r.conversation_closed,
        "trace_id": r.trace_id,
        "conversation_id": r.conversation_id,
        "status": r.status,
        "specialist": r.specialist,
        "handoff": r.handoff,
        "delivered_to_human": r.delivered_to_human,
        "awaiting_feedback": r.awaiting_feedback,
        "feedback": r.feedback,
    }
    return body, tasks


def client_messages(conversation_id: str, user_id: str, after: int = 0) -> dict:
    conv = store.get_conversation(conversation_id)
    if conv is None or conv["user_id"] != user_id:
        raise ApiError(404, "conversa não encontrada")
    return {
        "conversation": {"id": conv["id"], "status": conv["status"], "specialist": conv["assigned_agent"],
                         "awaiting_feedback": bool(conv["awaiting_feedback"])},
        "messages": store.list_messages(conversation_id, after),
    }


# --- especialista ----------------------------------------------------------------
def _agent_name(payload: dict) -> str:
    return _text(payload, "agent_name", 60)


def agent_queue() -> dict:
    items = []
    for c in store.queue():
        items.append({
            "id": c["id"], "user_id": c["user_id"], "status": c["status"],
            "reason": c["handoff_reason"], "theme": c["handoff_theme"],
            "assigned_agent": c["assigned_agent"], "waiting_s": _seconds_since(c["handoff_at"]),
            "last_client_message": c["last_client_message"],
            "awaiting_reply": c["last_author"] == "cliente",
        })
    return {"queue": items}


def agent_conversation(conversation_id: str) -> dict:
    conv = store.get_conversation(conversation_id)
    if conv is None:
        raise ApiError(404, "conversa não encontrada")
    return {
        "conversation": conv,
        "messages": store.list_messages(conversation_id),
        "context": store.conversation_context(conversation_id),
        "evaluations": store.list_evaluations(author="humano", conversation_id=conversation_id, limit=20),
    }


def agent_claim(conversation_id: str, payload: dict) -> dict:
    name = _agent_name(payload)
    try:
        conv = store.claim_conversation(conversation_id, name)
    except LookupError as exc:
        raise ApiError(404, str(exc)) from exc
    except PermissionError as exc:
        raise ApiError(409, str(exc)) from exc
    store.add_message(conversation_id, "sistema", f"{name} entrou na conversa.")
    return {"conversation": conv}


def agent_send(conversation_id: str, payload: dict) -> tuple[dict, list[Task]]:
    name = _agent_name(payload)
    text = mask_sensitive(_text(payload, "text", 4000))
    conv = store.get_conversation(conversation_id)
    if conv is None:
        raise ApiError(404, "conversa não encontrada")
    if conv["status"] != store.WITH_HUMAN or conv["assigned_agent"] != name:
        raise ApiError(409, "assuma a conversa antes de responder")
    # contexto para o avaliador: as mensagens do cliente desde a última resposta humana
    pending = []
    for m in store.list_messages(conversation_id):
        if m["author"] == "humano":
            pending = []
        elif m["author"] == "cliente":
            pending.append(m["text"])
    msg_id = store.add_message(conversation_id, "humano", text, author_name=name)
    client_text = "\n".join(pending[-3:]) or "(cliente aguardando retorno do especialista)"
    tasks: list[Task] = [(evaluate_safely, (f"h{msg_id}", client_text, text, []),
                          {"author": "humano", "agent_name": name, "conversation_id": conversation_id})]
    return {"message_id": msg_id}, tasks


def agent_close(conversation_id: str, payload: dict) -> dict:
    name = _agent_name(payload)
    try:
        conv = store.close_conversation(conversation_id, name)
    except LookupError as exc:
        raise ApiError(404, str(exc)) from exc
    except PermissionError as exc:
        raise ApiError(409, str(exc)) from exc
    store.add_message(conversation_id, "sistema", f"Atendimento encerrado por {name}.")
    return {"conversation": conv}


# --- qualidade -------------------------------------------------------------------
def quality_metrics() -> dict:
    return store.metrics()


def quality_evaluations(author: str | None = None, below: int | None = None, limit: int = 50) -> dict:
    if author not in (None, "", "ia", "humano"):
        raise ApiError(422, 'autor deve ser "ia" ou "humano"')
    return {"evaluations": store.list_evaluations(author=author or None, max_total=below, limit=min(limit, 200))}


def batch_start(payload: dict, ip: str) -> tuple[dict, list[Task]]:
    csv_text = _text(payload, "csv", 500_000)
    name = _text(payload, "name", 120, required=False) or "lote"
    sample = payload.get("sample")
    if sample is not None and (not isinstance(sample, int) or not 1 <= sample <= 100):
        raise ApiError(422, "sorteie de 1 a 100 perguntas")
    try:
        mode, rows = parse_csv(csv_text, sample=sample)
    except BatchError as exc:
        raise ApiError(422, str(exc)) from exc
    limiter.hit(ip, cost=len(rows))
    bid = store.create_batch(name, mode, len(rows))
    return {"id": bid, "mode": mode, "total": len(rows)}, [(run_batch, (bid, mode, rows), {})]


def batch_get(batch_id: str) -> dict:
    b = store.get_batch(batch_id)
    if b is None:
        raise ApiError(404, "lote não encontrado")
    return b


def batch_list() -> dict:
    return {"batches": store.list_batches()}

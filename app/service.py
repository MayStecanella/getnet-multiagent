"""Caso de uso de atendimento: executa o grafo, registra e prepara a avaliação."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from app.graph import build_graph
from app.observability.logging import log_event
from app.observability.store import save_interaction

log = logging.getLogger("service")

FALLBACK = (
    "Olá! Obrigado por falar com a Getnet.\n\n"
    "Tive uma instabilidade e não consegui concluir sua solicitação agora. Você pode tentar de novo "
    "em alguns minutos ou consultar a central Get Ajuda: https://site.getnet.com.br/get-ajuda/\n\n"
    "Obrigado pela paciência e até logo!"
)


@dataclass
class ChatResult:
    trace_id: str
    response: str
    triage: str
    agents: list[str]
    theme: str | None
    sources: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    conversation_closed: bool = False
    masked_message: str = ""
    error: bool = False


def handle_message(message: str, user_id: str) -> ChatResult:
    trace_id = uuid.uuid4().hex
    started = time.perf_counter()
    try:
        state = build_graph().invoke({"message": message, "user_id": user_id, "trace_id": trace_id, "outputs": []})
    except Exception:
        log.exception("falha no grafo", extra={"fields": {"trace_id": trace_id}})
        return ChatResult(trace_id=trace_id, response=FALLBACK, triage="erro", agents=[], theme=None, error=True)

    outputs = state.get("outputs", [])
    result = ChatResult(
        trace_id=trace_id,
        response=state["response"],
        triage=state["triage"],
        agents=[o.agent for o in outputs],
        theme=state.get("theme"),
        sources=list(dict.fromkeys(s for o in outputs for s in o.sources)),
        tools_used=list(dict.fromkeys(t for o in outputs for t in o.tools_used)),
        conversation_closed=bool(state.get("conversation_closed")),
        masked_message=state.get("masked_message", ""),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    recontacted = save_interaction({
        "trace_id": trace_id, "user_id": user_id, "message_masked": result.masked_message,
        "triage": result.triage, "routes": result.agents, "theme": result.theme,
        "response": result.response, "sources": result.sources, "latency_ms": latency_ms,
    })
    log_event(log, "atendimento", trace_id=trace_id, user_id=user_id, triage=result.triage,
              routes=state.get("routes"), agents=result.agents, theme=result.theme,
              router_confidence=state.get("router_confidence"), tools=result.tools_used,
              latency_ms=latency_ms, recontato_de=recontacted)
    return result

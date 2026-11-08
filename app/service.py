"""Caso de uso de atendimento: conversa, grafo de agentes, resolução e transbordo.

Regra do especialista humano: a IA sempre tenta resolver primeiro.
1. Toda resposta dos agentes (Knowledge, Autosserviço, Support) termina com "Consegui resolver?".
   - "sim": a IA agradece e encerra a conversa (resolução confirmada pelo cliente).
   - "não": a conversa vai para a fila do especialista, com todo o histórico.
   - outra pergunta: segue com a IA, que responde e pergunta de novo.
2. Se o cliente volta ao mesmo tema e chega à N-ésima tentativa (HANDOFF_AFTER_ATTEMPTS, padrão 4),
   a conversa vai para o especialista; a resposta da IA vira sugestão para ele.
3. Pedido de atendente sem assunto: a IA pede o assunto e tenta resolver.
4. Busca web e bloqueios (dado sensível, injection, fora de escopo) nunca vão para o especialista.
Depois do transbordo, as mensagens do cliente vão só para o especialista.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from app.agents.guardrails import classify_feedback, mask_sensitive
from app.config import settings
from app.graph import build_graph
from app.observability import store
from app.observability.logging import log_event

log = logging.getLogger("service")

FALLBACK = (
    "Olá! Obrigado por falar com a Getnet.\n\n"
    "Tive uma instabilidade e não consegui concluir sua solicitação agora. Você pode tentar de novo "
    "em alguns minutos ou consultar a central Get Ajuda: https://site.getnet.com.br/get-ajuda/\n\n"
    "Obrigado pela paciência e até logo!"
)

QUEUED_ACK = "Sua mensagem foi encaminhada ao especialista, e a resposta chega por aqui."

RESOLUTION_QUESTION = "Consegui resolver o seu pedido? Responda sim ou não."

CLOSING_RESOLVED = (
    "Que ótimo! Fico feliz em ter ajudado.\n\n"
    "Se precisar de mais alguma coisa, é só chamar por aqui. Obrigado por falar com a Getnet e até logo!"
)

HANDOFF_NOT_RESOLVED = (
    "Entendi, e obrigado por me avisar. Vou te transferir agora para um especialista Getnet, que "
    "continua daqui com todo o histórico desta conversa; você não precisa repetir nada.\n\n"
    "Próximo passo: é só aguardar nesta tela; a resposta chega por aqui."
)

HANDOFF_ATTEMPTS = (
    "Percebi que esse assunto ainda não ficou resolvido, então vou te transferir para um "
    "especialista Getnet, que continua daqui com todo o histórico desta conversa; você não precisa "
    "repetir nada.\n\n"
    "Próximo passo: é só aguardar nesta tela; a resposta chega por aqui."
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
    conversation_id: str = ""
    status: str = store.IA
    specialist: str | None = None
    handoff: bool = False             # esta mensagem gerou o transbordo
    delivered_to_human: bool = False  # a conversa já estava com o especialista
    awaiting_feedback: bool = False   # a resposta pergunta "consegui resolver?"
    feedback: str | None = None       # "sim" ou "nao", quando a mensagem respondeu à pergunta


def _open_conversation(user_id: str, conversation_id: str | None, channel: str) -> dict:
    if conversation_id:
        conv = store.get_conversation(conversation_id)
        if conv and conv["user_id"] == user_id and conv["status"] != store.CLOSED:
            return conv
    return store.create_conversation(user_id, channel)


def handle_message(message: str, user_id: str, conversation_id: str | None = None,
                   channel: str = "api") -> ChatResult:
    conv = _open_conversation(user_id, conversation_id, channel)
    cid = conv["id"]
    trace_id = uuid.uuid4().hex

    # Conversa já transbordada: a mensagem vai para o especialista, sem passar pela IA
    if conv["status"] in store.HUMAN_STATUSES:
        masked = mask_sensitive(message)
        store.add_message(cid, "cliente", masked)
        log_event(log, "mensagem_para_especialista", conversation_id=cid, user_id=user_id)
        return ChatResult(trace_id=trace_id, response=QUEUED_ACK, triage="humano", agents=[], theme=None,
                          masked_message=masked, conversation_id=cid, status=conv["status"],
                          specialist=conv["assigned_agent"], delivered_to_human=True)

    # Resposta à pergunta "consegui resolver?"
    if conv["awaiting_feedback"]:
        feedback = classify_feedback(message)
        if feedback is not None:
            return _handle_feedback(conv, message, feedback, trace_id)

    previous_themes = store.conversation_themes(cid)
    started = time.perf_counter()
    try:
        state = build_graph().invoke({"message": message, "user_id": user_id, "trace_id": trace_id, "outputs": []})
    except Exception:
        log.exception("falha no grafo", extra={"fields": {"trace_id": trace_id}})
        masked = mask_sensitive(message)
        store.add_message(cid, "cliente", masked)
        store.add_message(cid, "ia", FALLBACK)
        store.set_awaiting_feedback(cid, None)
        return ChatResult(trace_id=trace_id, response=FALLBACK, triage="erro", agents=[], theme=None,
                          masked_message=masked, error=True, conversation_id=cid)

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
        conversation_id=cid,
    )

    external = channel not in store.INTERNAL_CHANNELS
    attempts = previous_themes.count(result.theme) + 1 if result.theme else 0
    if (external and result.triage == "adquirencia" and result.theme
            and attempts >= settings.handoff_after_attempts):
        # tentativas esgotadas no mesmo tema: a resposta da IA vira sugestão para o especialista
        store.set_handoff(cid, "tentativas_esgotadas", theme=result.theme, suggestion=result.response)
        result.response = HANDOFF_ATTEMPTS
        result.handoff = True
        result.status = store.WAITING
    elif result.triage == "adquirencia":
        result.response = f"{result.response}\n\n{RESOLUTION_QUESTION}"
        result.awaiting_feedback = True

    store.add_message(cid, "cliente", result.masked_message)
    store.add_message(cid, "ia", result.response, trace_id=trace_id)
    if not result.handoff:
        store.set_awaiting_feedback(cid, trace_id if result.awaiting_feedback else None)

    latency_ms = int((time.perf_counter() - started) * 1000)
    recontacted = store.save_interaction({
        "trace_id": trace_id, "conversation_id": cid, "user_id": user_id, "channel": channel,
        "message_masked": result.masked_message, "triage": result.triage, "routes": result.agents,
        "theme": result.theme, "response": result.response, "sources": result.sources,
        "tools": result.tools_used, "latency_ms": latency_ms, "asked_feedback": result.awaiting_feedback,
    })
    log_event(log, "atendimento", trace_id=trace_id, conversation_id=cid, user_id=user_id, channel=channel,
              triage=result.triage, routes=state.get("routes"), agents=result.agents, theme=result.theme,
              router_confidence=state.get("router_confidence"), tools=result.tools_used,
              latency_ms=latency_ms, recontato_de=recontacted, transbordo=result.handoff,
              tentativa_no_tema=attempts)
    return result


def _handle_feedback(conv: dict, message: str, feedback: str, trace_id: str) -> ChatResult:
    """Cliente respondeu "sim" ou "não" à pergunta de resolução."""
    cid = conv["id"]
    masked = mask_sensitive(message)
    store.add_message(cid, "cliente", masked)
    store.record_feedback(conv["awaiting_feedback"], resolved=feedback == "sim")
    if feedback == "sim":
        store.add_message(cid, "ia", CLOSING_RESOLVED)
        store.close_by_client(cid)
        status, response, handoff = store.CLOSED, CLOSING_RESOLVED, False
    else:
        themes = store.conversation_themes(cid)
        store.set_handoff(cid, "nao_resolvido", theme=themes[-1] if themes else None)
        store.add_message(cid, "ia", HANDOFF_NOT_RESOLVED)
        status, response, handoff = store.WAITING, HANDOFF_NOT_RESOLVED, True
    log_event(log, "resposta_resolucao", conversation_id=cid, user_id=conv["user_id"], feedback=feedback,
              trace_respondido=conv["awaiting_feedback"])
    return ChatResult(trace_id=trace_id, response=response, triage="resolucao", agents=[], theme=None,
                      masked_message=masked, conversation_id=cid, status=status, handoff=handoff,
                      conversation_closed=feedback == "sim", feedback=feedback)

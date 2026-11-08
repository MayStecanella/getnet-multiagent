"""Estado compartilhado entre os nós do grafo e os contratos de dados."""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Triage = Literal["adquirencia", "web", "dado_sensivel", "injection", "fora_de_escopo", "humano"]
Route = Literal["knowledge", "self_service", "support"]


class AgentOutput(BaseModel):
    agent: str
    content: str = Field(description="Conteúdo factual produzido pelo agente, sem saudação")
    sources: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)


class GraphState(TypedDict, total=False):
    # entrada
    message: str
    user_id: str
    trace_id: str
    # triagem
    triage: Triage
    masked_message: str
    # roteamento
    routes: list[Route]
    theme: str
    router_confidence: float
    # saídas dos agentes (reducer: agentes em paralelo acumulam na mesma lista)
    outputs: Annotated[list[AgentOutput], operator.add]
    # resposta final
    response: str
    conversation_closed: bool

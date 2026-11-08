"""Orquestração: grafo LangGraph.

guardrails ──┬─ dado_sensivel / injection / fora_de_escopo ──> END (resposta padrão)
             ├─ humano (pedido de atendente) ──> END (transbordo feito pelo app.service)
             ├─ web ──> web_search ──> END
             └─ adquirencia ──> router ──┬─> knowledge ────┐
                                         ├─> self_service ─┼─> composer ──> END
                                         └─> support ──────┘
O router pode acionar mais de um agente em paralelo (gateway inclusivo); as saídas se
acumulam em `outputs` e o composer junta tudo. A Qualidade roda fora do grafo, depois da
resposta (ver app.service).
"""
from __future__ import annotations

from functools import lru_cache

from app.agents.composer import composer_node
from app.agents.guardrails import guardrails_node
from app.agents.knowledge import knowledge_node
from app.agents.router import router_node
from app.agents.self_service import self_service_node
from app.agents.support import support_node
from app.agents.web import web_node
from app.state import GraphState

ROUTE_TO_NODE = {"knowledge": "knowledge", "self_service": "self_service", "support": "support"}


def after_guardrails(state: GraphState) -> str:
    triage = state["triage"]
    if triage == "adquirencia":
        return "router"
    if triage == "web":
        return "web_search"
    return "end"


def after_router(state: GraphState) -> list[str]:
    return [ROUTE_TO_NODE[r] for r in state["routes"]]


@lru_cache
def build_graph():
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(GraphState)
    g.add_node("guardrails", guardrails_node)
    g.add_node("web_search", web_node)
    g.add_node("router", router_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("self_service", self_service_node)
    g.add_node("support", support_node)
    g.add_node("composer", composer_node)

    g.add_edge(START, "guardrails")
    g.add_conditional_edges("guardrails", after_guardrails,
                            {"router": "router", "web_search": "web_search", "end": END})
    g.add_edge("web_search", END)
    g.add_conditional_edges("router", after_router, ["knowledge", "self_service", "support"])
    for node in ("knowledge", "self_service", "support"):
        g.add_edge(node, "composer")
    g.add_edge("composer", END)
    return g.compile()

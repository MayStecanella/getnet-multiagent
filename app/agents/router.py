"""Router Agent: classifica a intenção e escolhe uma ou mais rotas (gateway inclusivo)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import prompts
from app.llm import get_llm
from app.state import GraphState, Route
from app.tools.catalog import catalog_names


class RouterDecision(BaseModel):
    routes: list[Route] = Field(min_length=1)
    theme: str = Field(description="rótulo curto em snake_case do assunto")
    confidence: float = Field(ge=0, le=1)


def normalize_routes(routes: list[Route]) -> list[Route]:
    """Regras de negócio aplicadas sobre a decisão do LLM."""
    ordered: list[Route] = []
    for r in routes:
        if r not in ordered:
            ordered.append(r)
    if "support" in ordered and "self_service" in ordered:
        ordered.remove("self_service")
    return ordered or ["support"]


def router_node(state: GraphState) -> GraphState:
    decision = get_llm().structured(
        prompts.ROUTER.format(catalog=catalog_names()),
        f"<mensagem>{state['masked_message']}</mensagem>",
        RouterDecision,
    )
    return {
        "routes": normalize_routes(decision.routes),
        "theme": decision.theme.strip().lower() or "geral",
        "router_confidence": decision.confidence,
    }

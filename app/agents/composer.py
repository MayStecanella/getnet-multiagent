"""Compositor: junta as saídas dos agentes em uma mensagem única no DNA Getnet."""
from __future__ import annotations

from app import prompts
from app.llm import get_llm
from app.state import GraphState


def composer_node(state: GraphState) -> GraphState:
    parts = "\n\n".join(
        f'<agente nome="{o.agent}" fontes="{", ".join(o.sources)}">\n{o.content}\n</agente>'
        for o in state.get("outputs", [])
    )
    user = f"Mensagem do cliente: {state['masked_message']}\n\n{parts}"
    return {"response": get_llm().text(prompts.COMPOSER, user), "conversation_closed": False}

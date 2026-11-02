"""Resposta de busca na web (triagem "web"): responde, agradece e encerra."""
from __future__ import annotations

from datetime import datetime

from app import prompts
from app.llm import get_llm
from app.state import AgentOutput, GraphState
from app.tools.web_search import web_search


def web_node(state: GraphState) -> GraphState:
    question = state["masked_message"]
    results = web_search(question)
    if not results:
        response = (
            "Olá! Obrigado pelo contato.\n\n"
            "No momento não consegui consultar a internet para responder a essa pergunta. "
            "Tente novamente em alguns minutos.\n\nObrigado e até logo!"
        )
        return {"response": response, "conversation_closed": True,
                "outputs": [AgentOutput(agent="web_search", content="sem resultados")]}

    context = "\n\n".join(f'<resultado fonte="{r.url}" titulo="{r.title}">\n{r.content}\n</resultado>' for r in results)
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    response = get_llm().text(prompts.WEB_ANSWER.format(now=now), f"Pergunta: {question}\n\n{context}")
    return {
        "response": response,
        "conversation_closed": True,
        "outputs": [AgentOutput(agent="web_search", content=response, sources=[r.url for r in results],
                                tools_used=["web_search"])],
    }

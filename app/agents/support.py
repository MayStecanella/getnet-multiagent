"""Customer Support Agent: planeja e executa tools sobre a conta do cliente.

Fluxo: o LLM planeja as chamadas (saída estruturada) -> o código executa as tools com o
user_id da requisição (o LLM nunca escolhe o user_id) -> o LLM responde com os dados e com
orientações do Get Ajuda recuperadas pelo RAG.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app import prompts
from app.agents.knowledge import format_chunks
from app.llm import get_llm
from app.rag.retriever import get_retriever
from app.state import AgentOutput, GraphState
from app.tools.account import TOOLS

log = logging.getLogger(__name__)


class ToolCall(BaseModel):
    name: Literal["get_settlements", "get_transactions", "get_terminal_status"]
    sales_date: str | None = None
    status: Literal["recusada", "aprovada", "todas"] | None = None


class SupportPlan(BaseModel):
    """Plano em campos fixos (sem lista livre): saída curta e previsível para o LLM."""

    use_settlements: bool = Field(description="consultar get_settlements")
    sales_date: str | None = Field(default=None, description="AAAA-MM-DD das vendas; vazio = ontem")
    use_transactions: bool = Field(description="consultar get_transactions")
    transactions_status: Literal["recusada", "aprovada", "todas"] = "todas"
    use_terminal_status: bool = Field(description="consultar get_terminal_status")

    def calls(self) -> list[ToolCall]:
        out = []
        if self.use_settlements:
            out.append(ToolCall(name="get_settlements", sales_date=self.sales_date or None))
        if self.use_transactions:
            out.append(ToolCall(name="get_transactions", status=self.transactions_status))
        if self.use_terminal_status:
            out.append(ToolCall(name="get_terminal_status"))
        return out


def run_tool(call: ToolCall, user_id: str) -> dict:
    fn = TOOLS[call.name]
    try:
        if call.name == "get_settlements":
            return fn(user_id, call.sales_date)
        if call.name == "get_transactions":
            return fn(user_id, call.status or "todas")
        return fn(user_id)
    except Exception as exc:  # uma tool com falha não derruba o atendimento
        log.exception("tool %s falhou", call.name)
        return {"found": False, "error": str(exc)}


def support_node(state: GraphState) -> GraphState:
    llm = get_llm()
    message = state["masked_message"]
    plan = llm.structured(
        prompts.SUPPORT_PLAN.format(today=date.today().isoformat()),
        f"<mensagem>{message}</mensagem>",
        SupportPlan,
    )
    results = {c.name: run_tool(c, state["user_id"]) for c in plan.calls()}

    try:
        guidance = get_retriever().search([message], k=3)
    except Exception:  # índice indisponível: segue só com os dados da conta
        log.warning("RAG indisponível para o Support")
        guidance = []

    user = (
        f"Mensagem: {message}\n\n<dados_conta>\n{json.dumps(results, ensure_ascii=False, indent=1)}\n</dados_conta>"
        f"\n\n<orientacoes>\n{format_chunks(guidance)}\n</orientacoes>"
    )
    answer = llm.text(prompts.SUPPORT_ANSWER, user)
    return {
        "outputs": [
            AgentOutput(
                agent="support",
                content=answer,
                tools_used=list(results),
                sources=list(dict.fromkeys(c.url for c in guidance if c.url)),
            )
        ]
    }

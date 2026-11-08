"""Resposta de busca na web (triagem "web"): prepara a busca, responde, agradece e encerra.

Perguntas atuais (placar do último jogo, classificação do Brasileirão, notícias) dependem da
data de hoje: o agente reescreve a pergunta com a data e busca em modo notícias, com janela de
tempo, e a resposta prioriza o resultado mais recente.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Literal

from pydantic import BaseModel

from app import prompts
from app.llm import get_llm
from app.state import AgentOutput, GraphState
from app.tools.web_search import web_search

log = logging.getLogger("web")

BRT = timezone(timedelta(hours=-3))  # horário de Brasília (sem horário de verão desde 2019)
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro")

NO_RESULTS = (
    "Olá! Obrigado pelo contato.\n\n"
    "No momento não consegui consultar a internet para responder a essa pergunta. "
    "Tente novamente em alguns minutos.\n\nObrigado e até logo!"
)


KIND_LABEL = {"resultado": "resultado (algo que já aconteceu)", "agenda": "agenda (algo que vai acontecer)",
              "classificacao": "classificação de campeonato", "outro": "pergunta geral"}


class WebQuery(BaseModel):
    kind: Literal["resultado", "agenda", "classificacao", "outro"] = "outro"
    query: str
    topic: Literal["general", "news"] = "general"
    recency: Literal["day", "week", "month", "any"] = "any"


def now_brt() -> datetime:
    return datetime.now(BRT)


def plan_query(question: str, today: datetime) -> WebQuery:
    system = prompts.WEB_QUERY.format(today=f"{today:%d/%m/%Y}", month_year=f"{MONTHS[today.month - 1]} {today.year}")
    try:
        plan = get_llm().structured(system, f"<pergunta>{question}</pergunta>", WebQuery)
        if plan.query.strip():
            return plan
    except Exception:  # sem plano, busca com a própria pergunta
        log.exception("falha ao preparar a busca")
    return WebQuery(query=question)


def format_results(resp) -> str:
    parts = [
        f'<resultado fonte="{escape(r.url)}" titulo="{escape(r.title)}" data="{escape(r.published or "sem data")}">\n'
        f"{r.content}\n</resultado>"
        for r in resp.results
    ]
    if resp.answer:
        parts.append(f'<resultado fonte="resumo do buscador" titulo="resumo" data="hoje">\n{resp.answer}\n</resultado>')
    return "\n\n".join(parts)


def web_node(state: GraphState) -> GraphState:
    question = state["masked_message"]
    today = now_brt()
    plan = plan_query(question, today)
    # agenda: o que importa é a data do evento, não a da publicação; mantém a ordem de relevância
    resp = web_search(plan.query, topic=plan.topic, recency=plan.recency, newest_first=plan.kind != "agenda")
    if not resp.results:
        return {"response": NO_RESULTS, "conversation_closed": True,
                "outputs": [AgentOutput(agent="web_search", content="sem resultados", tools_used=["web_search"])]}

    system = prompts.WEB_ANSWER.format(today=f"{today:%d/%m/%Y} ({WEEKDAYS[today.weekday()]})",
                                       now=f"{today:%d/%m/%Y %H:%M}", kind=KIND_LABEL[plan.kind])
    response = get_llm().text(system, f"Pergunta: {question}\n\n{format_results(resp)}")
    return {
        "response": response,
        "conversation_closed": True,
        "outputs": [AgentOutput(agent="web_search", content=response, sources=[r.url for r in resp.results],
                                tools_used=["web_search"])],
    }

"""Agente de Qualidade: roda depois que a resposta sai, sem bloquear nem alterar o atendimento.

Mede duas coisas:
- assertividade: pelo recontato no mesmo tema (aplicado em observability.store.save_interaction);
- DNA Getnet: LLM-as-judge (modelo separado do gerador), 0 a 25 por pilar, total 0 a 100.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app import prompts
from app.agents.guardrails import find_sensitive
from app.llm import get_judge_llm
from app.observability.logging import log_event
from app.observability.store import save_evaluation

log = logging.getLogger("quality")


class PillarScore(BaseModel):
    score: int = Field(ge=0, le=25)
    justification: str


class DnaEvaluation(BaseModel):
    util: PillarScore
    calmo: PillarScore
    amigavel: PillarScore
    empoderado: PillarScore
    sem_fonte: bool
    dado_sensivel: bool


def evaluate(trace_id: str, message: str, response: str, sources: list[str]) -> dict:
    user = (
        f"<mensagem_cliente>{message}</mensagem_cliente>\n"
        f"<fontes_disponiveis>{', '.join(sources) or 'nenhuma'}</fontes_disponiveis>\n"
        f"<resposta>{response}</resposta>"
    )
    ev = get_judge_llm().structured(prompts.JUDGE, user, DnaEvaluation)
    pillars = {"util": ev.util, "calmo": ev.calmo, "amigavel": ev.amigavel, "empoderado": ev.empoderado}
    result = {
        **{k: v.score for k, v in pillars.items()},
        "total": sum(v.score for v in pillars.values()),
        "justifications": {k: v.justification for k, v in pillars.items()},
        "alert_sem_fonte": ev.sem_fonte,
        # o alerta de dado sensível combina o juiz com a regra determinística
        "alert_dado_sensivel": ev.dado_sensivel or bool(find_sensitive(response)),
    }
    save_evaluation(trace_id, result)
    log_event(log, "avaliacao_dna", trace_id=trace_id, total=result["total"],
              alert_sem_fonte=result["alert_sem_fonte"], alert_dado_sensivel=result["alert_dado_sensivel"])
    return result


def evaluate_safely(*args, **kwargs) -> None:
    """Versão para background: uma falha do avaliador nunca afeta o cliente."""
    try:
        evaluate(*args, **kwargs)
    except Exception:
        log.exception("falha na avaliação de qualidade")

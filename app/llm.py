"""Camada fina sobre o LLM.

Os agentes dependem apenas do protocolo `LLM` (duas operações: texto livre e saída
estruturada). Assim, a implementação real (LangChain) e o LLM simulado dos testes
são intercambiáveis, e o provedor é trocado só por configuração.
"""
from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import settings

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    def text(self, system: str, user: str) -> str: ...

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...


class LangChainLLM:
    """Implementação real via `init_chat_model` (OpenAI, Anthropic, etc.)."""

    def __init__(self, model: str, provider: str, temperature: float = 0.0, max_tokens: int = 1500):
        from langchain.chat_models import init_chat_model  # import tardio

        # max_tokens limita respostas degeneradas (ex.: saída estruturada em loop)
        self._model = init_chat_model(model, model_provider=provider, temperature=temperature,
                                      max_tokens=max_tokens)

    def text(self, system: str, user: str) -> str:
        msg = self._model.invoke([("system", system), ("human", user)])
        return str(msg.content).strip()

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        runnable = self._model.with_structured_output(schema)
        return runnable.invoke([("system", system), ("human", user)])


_agent_llm: LLM | None = None
_judge_llm: LLM | None = None


def get_llm() -> LLM:
    global _agent_llm
    if _agent_llm is None:
        if settings.offline_mode:
            from app.offline import FakeLLM

            _agent_llm = FakeLLM()
        else:
            _agent_llm = LangChainLLM(settings.llm_model, settings.llm_provider)
    return _agent_llm


def get_judge_llm() -> LLM:
    """LLM do avaliador: modelo separado do gerador, para não virar autoavaliação."""
    global _judge_llm
    if _judge_llm is None:
        if settings.offline_mode:
            from app.offline import FakeLLM

            _judge_llm = FakeLLM()
        else:
            _judge_llm = LangChainLLM(settings.judge_model, settings.judge_provider)
    return _judge_llm


def set_llms(agent: LLM, judge: LLM | None = None) -> None:
    """Injeção usada pelos testes."""
    global _agent_llm, _judge_llm
    _agent_llm = agent
    _judge_llm = judge or agent

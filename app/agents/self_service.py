"""Autosserviço Agent: identifica a solicitação no catálogo e indica o canal digital.

O LLM só escolhe os ids; o texto vem do catálogo. Resposta determinística e sem links inventados.
"""
from __future__ import annotations

from pydantic import BaseModel

from app import prompts
from app.config import settings
from app.llm import get_llm
from app.state import AgentOutput, GraphState
from app.tools.catalog import catalog_for_prompt, load_catalog


class SelfServicePick(BaseModel):
    ids: list[str]


def channels_text() -> str:
    return f"pelo portal Minha Conta ({settings.portal_url}) ou pelo app Getnet ({settings.app_download_hint})"


def self_service_node(state: GraphState) -> GraphState:
    catalog = load_catalog()
    pick = get_llm().structured(
        prompts.SELF_SERVICE_PICK.format(catalog=catalog_for_prompt()),
        f"<mensagem>{state['masked_message']}</mensagem>",
        SelfServicePick,
    )
    items = [catalog[i] for i in dict.fromkeys(pick.ids) if i in catalog]

    if not items:
        content = (
            "Não identifiquei exatamente qual solicitação você quer fazer. As solicitações de serviço "
            f"ficam disponíveis {channels_text()}."
        )
    else:
        lines = [f"- {it['name']}: {it['description']}" for it in items]
        content = (
            "Solicitação identificada (você mesmo faz, de forma digital):\n"
            + "\n".join(lines)
            + f"\nOnde fazer: {channels_text()}."
        )
    return {
        "outputs": [
            AgentOutput(agent="self_service", content=content, sources=[settings.portal_url],
                        tools_used=[f"catalog:{it['id']}" for it in items])
        ]
    }

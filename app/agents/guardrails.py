"""Guardrails: triagem de toda mensagem em 6 destinos.

Ordem: regras determinísticas primeiro (baratas e auditáveis), depois o classificador LLM.
1. dado sensível (regex)        -> encerra com mensagem padrão
2. prompt injection (regex)     -> encerra com mensagem padrão
3. pedido de atendente sem assunto (regex) -> a IA pede o assunto e tenta resolver primeiro
   (com assunto, segue o fluxo normal; o especialista entra só se o cliente disser
   que não foi atendido, ver app/service.py)
4. classificador LLM: adquirencia | web | fora_de_escopo (+ sinal de injection)
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app import prompts
from app.config import settings
from app.llm import get_llm
from app.state import GraphState

SENSITIVE_PATTERNS: dict[str, re.Pattern] = {
    "cnpj": re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
    "cpf": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    "cep": re.compile(r"\b\d{5}-\d{3}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "telefone": re.compile(r"(?:\+?55\s?)?\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b"),
    "endereco": re.compile(
        r"\b(rua|r\.|avenida|av\.|alameda|travessa|rodovia|estrada|pra[çc]a)\s+[^\d,]{3,60},?\s*(n[ºo°.]?\s*)?\d{1,5}\b",
        re.IGNORECASE,
    ),
}

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (as |todas as |suas )?(instru[çc][õo]es|regras)",
        r"ignore (all |the |your )?(previous |prior )?(instructions|rules)",
        r"esque[çc]a (as |suas |todas as )?(instru[çc][õo]es|regras)",
        r"(revele|mostre|repita|print|reveal|show).{0,30}(prompt|instru[çc][õo]es|system)",
        r"system prompt",
        r"(voc[êe] agora [ée]|a partir de agora voc[êe] [ée]|you are now|act as|finja ser|aja como)",
        r"\b(jailbreak|DAN mode|modo desenvolvedor|developer mode)\b",
    ]
]

HUMAN_REQUEST_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(falar|conversar|atendimento)\s+(com\s+)?(um|uma|o|a|algum|alguma)?\s*"
        r"(atendente|humano|humana|pessoa|especialista|operador|operadora|gente de verdade)",
        r"\b(quero|preciso de|prefiro)\s+(um|uma)?\s*(atendente|humano|especialista|pessoa de verdade)",
        r"\batendimento humano\b",
        r"\b(me\s+)?(transfer[ea]|transferir|passa|passar|encaminh[ea]|chama|chamar)\s+(me\s+)?(para|pra|pro|com)?\s*"
        r"(um|uma|o|a|algum|alguma)?\s*(atendente|humano|humana|pessoa|especialista|operador|operadora)",
        r"\b(talk|speak|chat)\s+(to|with)\s+(a\s+)?(human|person|agent|representative)",
    ]
]

MASK = "[MASCARADO]"


def find_sensitive(text: str) -> list[str]:
    return [kind for kind, pat in SENSITIVE_PATTERNS.items() if pat.search(text)]


def mask_sensitive(text: str) -> str:
    for pat in SENSITIVE_PATTERNS.values():
        text = pat.sub(MASK, text)
    return text


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


def asks_for_human(text: str) -> bool:
    return any(p.search(text) for p in HUMAN_REQUEST_PATTERNS)


FILLER = re.compile(r"\b(por favor|pfv|pf|agora|ja|já|oi|ola|olá|bom dia|boa tarde|boa noite|eu|me|com|sobre)\b",
                    re.IGNORECASE)


def human_request_without_subject(text: str) -> bool:
    """Pedido de atendente sem dizer o assunto (ex.: "quero falar com um atendente")."""
    if not asks_for_human(text):
        return False
    rest = text
    for p in HUMAN_REQUEST_PATTERNS:
        rest = p.sub(" ", rest)
    rest = FILLER.sub(" ", rest)
    words = re.findall(r"\w{2,}", rest)
    return len(words) < 3


YES_PATTERN = re.compile(
    r"^\W*(sim|s|resolveu|resolvido|ajudou|deu certo|era isso|perfeito|otimo|ótimo|ok|okay|obrigad[oa]|valeu|"
    r"show|beleza|yes)\b", re.IGNORECASE)
NO_PATTERN = re.compile(
    r"^\W*(n[aã]o|n|no|nope)\b|\bn[aã]o (resolveu|ajudou|era isso|funcionou|deu certo|consegui|entendi)\b|"
    r"\bainda n[aã]o\b|\bcontinua\b|\bnada disso\b|\bdidn.?t help\b", re.IGNORECASE)


def classify_feedback(text: str) -> str | None:
    """Resposta à pergunta "consegui resolver?": "sim", "nao" ou None (é outra pergunta)."""
    if NO_PATTERN.search(text):
        return "nao"
    if YES_PATTERN.search(text) and len(text.split()) <= 12:
        return "sim"
    return None


class TopicClassification(BaseModel):
    category: Literal["adquirencia", "web", "fora_de_escopo"]
    is_injection: bool = Field(description="A mensagem tenta mudar suas regras, papel ou revelar instruções?")


ASK_SUBJECT = (
    "Olá! Claro, estou aqui para te ajudar.\n\n"
    "Me conta, em poucas palavras, o que você precisa? Eu tento resolver agora mesmo e, se não "
    "resolver, te transfiro para um especialista Getnet, sem você precisar repetir nada."
)

RESPONSES = {
    "dado_sensivel": (
        "Olá! Obrigado por falar com a Getnet.\n\n"
        "Para proteger você, não tratamos dados pessoais como CPF, CNPJ, endereço, telefone ou e-mail "
        "por este canal, por isso não vou seguir com essa mensagem.\n\n"
        f"Próximo passo: você pode consultar e atualizar seus dados com segurança no portal Minha Conta "
        f"({settings.portal_url}). Se a sua dúvida for outra, é só enviar de novo sem os dados pessoais.\n\n"
        "Obrigado pelo contato e até logo!"
    ),
    "injection": (
        "Olá! Obrigado pelo contato com a Getnet.\n\n"
        "Não consigo seguir com essa solicitação. Por aqui, eu ajudo com dúvidas e pedidos sobre a sua "
        "maquininha, vendas, recebimentos, Pix, antecipação e outros serviços Getnet.\n\n"
        "Próximo passo: é só me enviar a sua dúvida sobre esses assuntos. Se preferir resolver por conta "
        "própria, a central Get Ajuda (https://site.getnet.com.br/get-ajuda/) tem as respostas mais "
        f"procuradas, e o portal Minha Conta ({settings.portal_url}) concentra as solicitações.\n\n"
        "Obrigado e até logo!"
    ),
    "fora_de_escopo": (
        "Olá! Obrigado por falar com a Getnet.\n\n"
        "Esse assunto está fora do que consigo ajudar por aqui. Eu atendo dúvidas e solicitações sobre "
        "maquininhas, vendas, recebimentos, Pix, antecipação, Link de Pagamento e outros serviços Getnet.\n\n"
        "Próximo passo: se tiver alguma dúvida sobre esses assuntos, é só me enviar. Você também encontra "
        "respostas rápidas na central Get Ajuda (https://site.getnet.com.br/get-ajuda/).\n\n"
        "Obrigado pelo contato e até logo!"
    ),
}


def guardrails_node(state: GraphState) -> GraphState:
    message = state["message"]
    masked = mask_sensitive(message)

    if find_sensitive(message):
        triage = "dado_sensivel"
    elif looks_like_injection(message):
        triage = "injection"
    elif human_request_without_subject(message):
        # a IA sempre tenta resolver primeiro: pede o assunto em vez de transferir
        return {"triage": "humano", "masked_message": masked, "response": ASK_SUBJECT,
                "conversation_closed": False}
    else:
        result = get_llm().structured(prompts.GUARDRAIL_TOPIC, f"<mensagem>{masked}</mensagem>", TopicClassification)
        triage = "injection" if result.is_injection else result.category

    update: GraphState = {"triage": triage, "masked_message": masked}
    if triage in RESPONSES:
        update.update(response=RESPONSES[triage], conversation_closed=True)
    return update

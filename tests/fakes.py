"""LLM e retriever simulados: testes rápidos, determinísticos e sem chave de API.

O FakeLLM decide por palavras-chave, imitando o que o LLM real faria nos cenários do
enunciado. Ele registra as chamadas para os testes verificarem o que cada agente pediu.
"""
from __future__ import annotations

import re
import unicodedata

from app import prompts
from app.rag.retriever import Chunk


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _message(user: str) -> str:
    m = re.search(r"<mensagem>(.*?)</mensagem>", user, re.S)
    return _norm(m.group(1) if m else user)


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    # --- saída estruturada -------------------------------------------------
    def structured(self, system, user, schema):
        self.calls.append(("structured", schema.__name__, user))
        handler = getattr(self, f"_{schema.__name__}")
        return schema.model_validate(handler(_message(user), user))

    def _TopicClassification(self, msg, _):
        if _has(msg, "tempo", "previsao", "chover", "cotacao", "dolar", "weather", "exchange rate"):
            return {"category": "web", "is_injection": False}
        if _has(msg, "poema", "futebol", "receita de bolo"):
            return {"category": "fora_de_escopo", "is_injection": False}
        return {"category": "adquirencia", "is_injection": False}

    def _RouterDecision(self, msg, _):
        if _has(msg, "cai o dinheiro", "deposit", "recebo", "receber o dinheiro"):
            return {"routes": ["support"], "theme": "recebimento", "confidence": 0.9}
        if _has(msg, "recusad", "decline", "nao passa"):
            return {"routes": ["support"], "theme": "venda_recusada", "confidence": 0.9}
        if _has(msg, "conecta", "connect", "internet", "sem sinal"):
            return {"routes": ["support"], "theme": "conexao_maquina", "confidence": 0.85}
        if _has(msg, "bobina"):
            return {"routes": ["self_service"], "theme": "bobinas", "confidence": 0.95}
        if "antecip" in msg:
            routes = ["knowledge", "self_service"] if _has(msg, "como funciona", "how does") else ["self_service"]
            if _has(msg, "quero", "como funciona", "how does"):
                return {"routes": routes, "theme": "antecipacao", "confidence": 0.8}
        if "pix" in msg:
            return {"routes": ["knowledge"], "theme": "pix", "confidence": 0.9}
        if _has(msg, "crediario", "parcel"):
            return {"routes": ["knowledge"], "theme": "crediario", "confidence": 0.9}
        if _has(msg, "whatsapp", "link de pagamento", "payment link"):
            return {"routes": ["knowledge"], "theme": "link_pagamento", "confidence": 0.9}
        if _has(msg, "smart", "classica"):
            return {"routes": ["knowledge"], "theme": "modelos_maquina", "confidence": 0.9}
        return {"routes": ["support"], "theme": "geral", "confidence": 0.5}

    def _Queries(self, msg, _):
        return {"queries": [msg[:80]]}

    def _SelfServicePick(self, msg, _):
        ids = []
        if "bobina" in msg:
            ids.append("bobinas")
        if "antecip" in msg:
            ids.append("antecipacao")
        if "pix" in msg:
            ids.append("habilitacao_pix")
        return {"ids": ids}

    def _SupportPlan(self, msg, _):
        return {
            "use_settlements": _has(msg, "cai o dinheiro", "deposit", "recebo", "caiu"),
            "use_transactions": _has(msg, "recusad", "decline"),
            "transactions_status": "recusada",
            "use_terminal_status": _has(msg, "conecta", "connect", "internet", "decline"),
        }

    def _DnaEvaluation(self, msg, user):
        response = _norm(re.search(r"<resposta>(.*?)</resposta>", user, re.S).group(1))
        has_next = _has(response, "proximo passo", "acesse", "portal", "app")
        return {
            "util": {"score": 22, "justification": "linguagem simples"},
            "calmo": {"score": 23 if has_next else 10, "justification": "próximo passo" if has_next else "sem próximo passo"},
            "amigavel": {"score": 24, "justification": "tom cordial"},
            "empoderado": {"score": 20, "justification": "mostra onde agir"},
            "sem_fonte": False,
            "dado_sensivel": False,
        }

    # --- texto livre -------------------------------------------------------
    def text(self, system, user):
        self.calls.append(("text", system[:40], user))
        if system == prompts.COMPOSER:
            parts = re.findall(r"<agente[^>]*>\n(.*?)\n</agente>", user, re.S)
            return "Olá! Que bom falar com você.\n\n" + "\n\n".join(parts) + \
                "\n\nPróximo passo: se precisar, acesse o portal Minha Conta.\n\nObrigado e até logo!"
        if system == prompts.KNOWLEDGE:
            urls = re.findall(r'url="([^"]+)"', user)
            return f"Resposta baseada na base de conhecimento. Fonte: {urls[0] if urls else '-'}"
        if system == prompts.SUPPORT_ANSWER:
            return "Resposta do suporte com os dados da conta: " + user.split("<dados_conta>")[1][:200]
        return "Resposta da web. Obrigado e até logo!"


class FakeRetriever:
    def __init__(self, chunks: list[Chunk] | None = None):
        self.queries: list[list[str]] = []
        self.chunks = chunks if chunks is not None else [
            Chunk("A Get Smart é uma maquininha com tela touch...", "https://site.getnet.com.br/get-ajuda/maquininhas", "Maquininhas"),
            Chunk("Com o Pix você recebe na hora...", "https://site.getnet.com.br/get-ajuda/pix", "Pix"),
        ]

    def search(self, queries, k):
        self.queries.append(queries)
        return self.chunks

"""Modo off-line: LLM, RAG e busca web simulados.

Usado de duas formas:
- pelos testes (rápidos, determinísticos e sem chave);
- pela aplicação com OFFLINE_MODE=true, para demonstrar as telas sem internet e sem custo.

O FakeLLM decide por palavras-chave, imitando o que o LLM real faria nos cenários do desafio.
Ele registra as chamadas para os testes verificarem o que cada agente pediu.
"""
from __future__ import annotations

import json
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


def _has_word(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _br_date(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}"


WEB_WORDS = ("tempo em", "previsao", "chover", "cotacao", "dolar", "euro hoje", "weather", "exchange rate",
             "placar", "jogo do", "jogo da", "classificacao", "brasileirao", "campeonato", "libertadores",
             "copa do", "formula 1", "noticia", "quem ganhou", "quem venceu", "eleic", "selecao", "temperatura", "selic", "bitcoin", "capital da", "presidente", "feriado")
SPORTS_WORDS = ("placar", "jogo", "classificacao", "brasileirao", "campeonato", "libertadores", "quem ganhou",
                "quem venceu", "formula 1", "copa do", "selecao")


SELF_SERVICE_WORDS = {
    "bobinas": ("bobina",),
    "cancelamento_vendas": ("cancelar uma venda", "cancelar venda"),
    "domicilio_bancario": ("conta onde recebo", "domicilio"),
    "alteracao_endereco": ("mudar o endereco", "alterar o endereco"),
    "instalacao_adicional": ("segunda maquininha", "outra maquininha"),
    "extrato_eletronico": ("extrato eletronico",),
    "representante_legal": ("representante legal",),
}


class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    # --- saída estruturada -------------------------------------------------
    def structured(self, system, user, schema):
        self.calls.append(("structured", schema.__name__, user))
        handler = getattr(self, f"_{schema.__name__}")
        return schema.model_validate(handler(_message(user), user))

    def _TopicClassification(self, msg, _):
        if _has(msg, "poema", "receita de bolo", "voce acha", "piada", "palpite"):
            return {"category": "fora_de_escopo", "is_injection": False}
        if _has(msg, *WEB_WORDS):
            return {"category": "web", "is_injection": False}
        return {"category": "adquirencia", "is_injection": False}

    def _RouterDecision(self, msg, _):
        if _has(msg, "cai o dinheiro", "deposit", "recebo", "receber o dinheiro", "caiu", "recebimento"):
            return {"routes": ["support"], "theme": "recebimento", "confidence": 0.9}
        if _has(msg, "recusad", "decline", "nao passa"):
            return {"routes": ["support"], "theme": "venda_recusada", "confidence": 0.9}
        if _has(msg, "conecta", "connect", "internet", "sem sinal"):
            return {"routes": ["support"], "theme": "conexao_maquina", "confidence": 0.85}
        for sid, words in SELF_SERVICE_WORDS.items():
            if _has(msg, *words) and not _has(msg, "o que e", "como funciona"):
                return {"routes": ["self_service"], "theme": sid, "confidence": 0.9}
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
        if _has(msg, "smart", "classica", "bandeira", "comprovante", "vende na rua", "maquininha e melhor"):
            return {"routes": ["knowledge"], "theme": "modelos_maquina", "confidence": 0.9}
        return {"routes": ["support"], "theme": "geral", "confidence": 0.5}

    def _WebQuery(self, _msg, user):
        question = _norm(re.search(r"<pergunta>(.*?)</pergunta>", user, re.S).group(1))
        if _has(question, "proximo", "quando e", "calendario", "agenda"):
            return {"kind": "agenda", "query": question[:120], "topic": "general", "recency": "any"}
        news = _has(question, *SPORTS_WORDS, "noticia", "eleic")
        kind = "classificacao" if _has(question, "classificacao", "posicao", "lider") else "resultado" if news else "outro"
        return {"kind": kind, "query": question[:120], "topic": "news" if news else "general",
                "recency": "week" if news else "any"}

    def _Queries(self, msg, _):
        return {"queries": [msg[:80]]}

    def _SelfServicePick(self, msg, _):
        ids = [sid for sid, words in SELF_SERVICE_WORDS.items() if sid != "antecipacao" and _has(msg, *words)]
        if "bobina" in msg and "bobinas" not in ids:
            ids.append("bobinas")
        if "antecip" in msg:
            ids.append("antecipacao")
        if "pix" in msg:
            ids.append("habilitacao_pix")
        return {"ids": ids}

    def _SupportPlan(self, msg, _):
        return {
            "use_settlements": _has(msg, "cai o dinheiro", "deposit", "recebo", "caiu", "recebimento"),
            "use_transactions": _has(msg, "recusad", "decline"),
            "transactions_status": "recusada",
            "use_terminal_status": _has(msg, "conecta", "connect", "internet", "decline"),
        }

    def _DnaEvaluation(self, msg, user):
        response = _norm(re.search(r"<resposta>(.*?)</resposta>", user, re.S).group(1))
        short = len(response) < 60
        has_next = _has(response, "proximo passo", "acesse", "portal")
        warm = _has_word(response, "ola", "oi", "obrigado", "obrigada", "ate logo", "bom dia", "boa tarde", "boa noite")
        return {
            "util": {"score": 12 if short else 22, "justification": "resposta curta demais" if short else "linguagem simples"},
            "calmo": {"score": 23 if has_next else 10,
                      "justification": "deixa claro o próximo passo" if has_next else "sem próximo passo"},
            "amigavel": {"score": 24 if warm else 14, "justification": "tom cordial" if warm else "faltou cordialidade"},
            "empoderado": {"score": 20 if has_next else 12,
                           "justification": "mostra onde agir" if has_next else "não mostra onde o cliente age"},
            "sem_fonte": False,
            "dado_sensivel": False,
        }

    # --- texto livre -------------------------------------------------------
    def text(self, system, user):
        self.calls.append(("text", system[:40], user))
        if system == prompts.COMPOSER:
            parts = re.findall(r"<agente[^>]*>\n(.*?)\n</agente>", user, re.S)
            return ("Olá! Que bom falar com você.\n\n" + "\n\n".join(parts)
                    + "\n\nPróximo passo: se precisar, acesse o portal Minha Conta.")
        if system == prompts.KNOWLEDGE:
            chunks = re.findall(r'<trecho url="([^"]+)"[^>]*>\n(.*?)\n</trecho>', user, re.S)
            if not chunks:
                return "Não encontrei essa informação na base de conhecimento."
            url, text = chunks[0]
            return f"{text.strip()}\n\nFonte: {url}"
        if system == prompts.SUPPORT_ANSWER:
            return self._support_answer(user)
        if system.startswith(prompts.DNA_GETNET) and "<resultado" in user:
            m = re.search(r'<resultado fonte="([^"]+)"[^>]*>\n(.*?)\n</resultado>', user, re.S)
            return (f"Olá! Encontrei isto na internet:\n\n{m.group(2).strip()}\n\nFonte: {m.group(1)}\n\n"
                    "Obrigado pelo contato e até logo!")
        return "Resposta da busca na web (modo off-line). Obrigado pelo contato e até logo!"

    @staticmethod
    def _support_answer(user: str) -> str:
        raw = user.split("<dados_conta>")[1].split("</dados_conta>")[0]
        data = json.loads(raw)
        lines = []
        if "get_settlements" in data and data["get_settlements"].get("found"):
            s = data["get_settlements"]
            lines.append(f"Pelo seu plano ({s['settlement_plan'].lower()}), as vendas de "
                         f"{_br_date(s['sales_date'])} caem assim:")
            lines += [f"- {_br_date(d)}: {_brl(v)}" for d, v in s["net_by_date"].items()]
        if "get_transactions" in data and data["get_transactions"].get("found"):
            for t in data["get_transactions"]["transactions"]:
                if t["status"] == "recusada":
                    lines.append(f"A venda de {_brl(t['amount'])} foi recusada: {t['decline_reason'].lower()}.")
        if "get_terminal_status" in data and data["get_terminal_status"].get("found"):
            for t in data["get_terminal_status"]["terminals"]:
                lines.append(f"Sua {t['model']} ({t['connection']}) está com {t['note'].lower()}. "
                             "Aproxime a máquina do roteador ou troque para o chip de dados.")
        return "\n".join(lines) or "Não encontrei dados da sua conta para essa dúvida; confira no portal Minha Conta."


DEFAULT_CHUNKS = [
    Chunk("A Get Smart é a maquininha com tela touch e acesso a aplicativos; a Get Clássica é a "
          "opção mais simples, com impressora e conexão por chip.",
          "https://site.getnet.com.br/get-ajuda/maquininhas", "Maquininhas"),
    Chunk("Com o Pix você recebe na hora, inclusive em fins de semana, direto na sua conta.",
          "https://site.getnet.com.br/get-ajuda/pix", "Pix"),
]


class FakeRetriever:
    def __init__(self, chunks: list[Chunk] | None = None):
        self.queries: list[list[str]] = []
        self.chunks = list(DEFAULT_CHUNKS) if chunks is None else chunks

    def search(self, queries, k):
        self.queries.append(queries)
        return self.chunks


def fake_web_results(query: str):
    from app.tools.web_search import SearchResult

    if _has(_norm(query), *SPORTS_WORDS):
        return [SearchResult("Resultado simulado de esporte (modo off-line)", "https://exemplo.com/esportes",
                             "Resultado de exemplo, sem acesso à internet: no modo on-line, aqui entram o placar, "
                             "a competição e a data do jogo, ou a tabela do campeonato.", "2026-01-01")]
    return [SearchResult("Resultado simulado (modo off-line)", "https://exemplo.com/busca",
                         "Este é um resultado de exemplo, usado sem acesso à internet.")]

"""Especialista humano: a IA sempre tenta primeiro; transfere no "não" ou na 4ª tentativa no mesmo tema."""
import pytest

from app.agents.guardrails import (
    asks_for_human, classify_feedback, guardrails_node, human_request_without_subject,
)
from app.observability import store
from app.service import (
    CLOSING_RESOLVED, HANDOFF_ATTEMPTS, HANDOFF_NOT_RESOLVED, QUEUED_ACK, RESOLUTION_QUESTION, handle_message,
)

WEB = "web"


def ask(text, conv=None, user="cliente1988", channel=WEB):
    return handle_message(text, user, conv, channel=channel)


# --- reconhecimento de texto --------------------------------------------------------
@pytest.mark.parametrize("text", ["Quero falar com um atendente", "preciso de um humano",
                                  "quero atendimento humano por favor", "Can I talk to a human?"])
def test_detects_human_request_without_subject(text):
    assert asks_for_human(text) and human_request_without_subject(text)


def test_human_request_with_subject_goes_to_the_ai():
    text = "Quero falar com um atendente sobre minha venda recusada de ontem"
    assert asks_for_human(text) and not human_request_without_subject(text)


@pytest.mark.parametrize("text,expected", [
    ("Sim", "sim"), ("sim, resolveu", "sim"), ("Obrigado!", "sim"), ("ok", "sim"),
    ("Não", "nao"), ("não resolveu", "nao"), ("Ainda não caiu nada", "nao"), ("nao era isso", "nao"),
    ("E quanto custa a Get Smart?", None), ("Como funciona o Pix?", None),
])
def test_classify_feedback(text, expected):
    assert classify_feedback(text) == expected


# --- IA tenta sempre primeiro ----------------------------------------------------------
def test_human_request_without_subject_asks_what_the_client_needs(fake_llm):
    out = guardrails_node({"message": "Quero falar com um atendente", "user_id": "u"})
    assert out["triage"] == "humano" and out["conversation_closed"] is False
    assert "o que você precisa" in out["response"]
    assert fake_llm.calls == []
    r = ask("Quero falar com um atendente")
    assert not r.handoff and r.status == store.IA and store.queue() == []


def test_agent_answers_end_with_resolution_question():
    r = ask("Quando cai o dinheiro das vendas de ontem?")
    assert r.response.endswith(RESOLUTION_QUESTION) and r.awaiting_feedback
    assert store.get_conversation(r.conversation_id)["awaiting_feedback"] == r.trace_id


@pytest.mark.parametrize("text", ["Qual a cotação do euro hoje?", "Me escreve um poema",
                                  "Meu CPF é 123.456.789-09"])
def test_web_and_blocks_never_ask_and_never_handoff(text):
    r = ask(text)
    assert not r.awaiting_feedback and RESOLUTION_QUESTION not in r.response
    after = ask("Não", r.conversation_id) if not r.conversation_closed else None
    assert store.queue() == []
    if after:
        assert not after.handoff


# --- "sim" e "não" ---------------------------------------------------------------------
def test_yes_closes_and_counts_as_resolved():
    first = ask("Quero pedir bobinas")
    r = ask("Sim, resolveu!", first.conversation_id)
    assert r.response == CLOSING_RESOLVED and r.status == store.CLOSED and r.conversation_closed
    assert store.get_interaction(first.trace_id)["feedback"] == 1
    res = store.metrics()["resolucao"]
    assert res["resolvidas"] == 1 and res["taxa_resolucao_confirmada"] == 100.0


def test_no_hands_off_to_specialist_with_history():
    first = ask("Quando cai o dinheiro das vendas de ontem?")
    r = ask("Não", first.conversation_id)
    assert r.handoff and r.status == store.WAITING and r.response == HANDOFF_NOT_RESOLVED
    conv = store.get_conversation(first.conversation_id)
    assert conv["handoff_reason"] == "nao_resolvido" and conv["handoff_theme"] == "recebimento"
    assert store.get_interaction(first.trace_id)["feedback"] == 0
    assert [q["id"] for q in store.queue()] == [first.conversation_id]


def test_new_question_instead_of_answer_continues_with_ai():
    first = ask("Quando cai o dinheiro das vendas de ontem?")
    r = ask("E como funciona o Pix?", first.conversation_id)
    assert not r.handoff and r.awaiting_feedback and r.conversation_id == first.conversation_id
    assert store.get_interaction(first.trace_id)["feedback"] is None


# --- tentativas no mesmo tema -----------------------------------------------------------
def test_fourth_question_on_same_theme_goes_to_specialist_with_ai_suggestion():
    r = ask("Quando cai o dinheiro das vendas de ontem?")
    cid = r.conversation_id
    for text in ["E o recebimento do crédito?", "Quando recebo o débito?"]:
        r = ask(text, cid)
        assert not r.handoff  # 2ª e 3ª tentativas seguem com a IA
    r = ask("Ainda quero saber quando recebo", cid)  # contém "ainda" mas não é "ainda não": é pergunta
    assert r.handoff and r.response == HANDOFF_ATTEMPTS
    conv = store.get_conversation(cid)
    assert conv["handoff_reason"] == "tentativas_esgotadas" and "R$" in conv["ai_suggestion"]


def test_different_themes_do_not_count_as_attempts():
    r = ask("Quando cai o dinheiro das vendas de ontem?")
    for text in ["Como funciona o Pix?", "Quero pedir bobinas", "Quantas parcelas no crediário?"]:
        r = ask(text, r.conversation_id)
        assert not r.handoff


def test_internal_channels_never_reach_the_queue():
    r = ask("Quando cai o dinheiro das vendas de ontem?", channel="batch")
    ask("Não", r.conversation_id, channel="batch")
    assert store.queue() == []


# --- depois do transbordo ---------------------------------------------------------------
def test_messages_after_handoff_go_only_to_the_specialist(fake_llm):
    first = ask("Quando cai o dinheiro das vendas de ontem?")
    ask("Não", first.conversation_id)
    calls_before = len(fake_llm.calls)
    r = ask("Alguém pode me ajudar?", first.conversation_id)
    assert r.delivered_to_human and r.response == QUEUED_ACK
    assert len(fake_llm.calls) == calls_before


def test_claim_and_close_lifecycle():
    first = ask("Quando cai o dinheiro das vendas de ontem?")
    cid = ask("Não", first.conversation_id).conversation_id
    store.claim_conversation(cid, "Ana")
    with pytest.raises(PermissionError):
        store.claim_conversation(cid, "Bruno")
    with pytest.raises(PermissionError):
        store.close_conversation(cid, "Bruno")
    assert store.close_conversation(cid, "Ana")["status"] == store.CLOSED
    nxt = ask("Oi de novo", cid)
    assert nxt.conversation_id != cid and nxt.status == store.IA


def test_conversation_of_another_user_is_not_reused():
    r = ask("Quero pedir bobinas")
    other = ask("Quero pedir bobinas", r.conversation_id, user="outro_cliente")
    assert other.conversation_id != r.conversation_id


def test_old_database_is_migrated(tmp_path, monkeypatch):
    """Um banco da versão anterior (sem as colunas novas) é atualizado sem perder dados."""
    import sqlite3
    from dataclasses import replace

    db = tmp_path / "atendimento.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, channel TEXT NOT NULL, "
                 "internal INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at TEXT NOT NULL, "
                 "updated_at TEXT NOT NULL, handoff_reason TEXT, handoff_at TEXT, handoff_theme TEXT, "
                 "ai_suggestion TEXT, assigned_agent TEXT, claimed_at TEXT, closed_at TEXT)")
    conn.execute("INSERT INTO conversations (id, user_id, channel, status, created_at, updated_at) "
                 "VALUES ('antiga', 'u', 'web', 'ia', 'x', 'x')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(store, "settings", replace(store.settings, data_dir=tmp_path))
    assert store.get_conversation("antiga")["awaiting_feedback"] is None

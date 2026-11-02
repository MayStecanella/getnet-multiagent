from datetime import datetime, timedelta, timezone

from app.agents.quality import evaluate
from app.observability.store import get_interaction, metrics, save_interaction


def _rec(trace, theme, user="cliente1988"):
    return {"trace_id": trace, "user_id": user, "message_masked": "m", "triage": "adquirencia",
            "routes": ["support"], "theme": theme, "response": "r", "sources": [], "latency_ms": 10}


def test_recontact_same_theme_marks_previous_as_unresolved():
    now = datetime.now(timezone.utc)
    save_interaction(_rec("a", "recebimento"), now=now - timedelta(hours=2))
    marked = save_interaction(_rec("b", "recebimento"), now=now)
    assert marked == "a"
    assert get_interaction("a")["recontacted"] == 1
    assert get_interaction("b")["recontacted"] == 0


def test_no_recontact_outside_window_or_other_theme_or_user():
    now = datetime.now(timezone.utc)
    save_interaction(_rec("a", "recebimento"), now=now - timedelta(hours=30))
    save_interaction(_rec("b", "pix"), now=now - timedelta(hours=1))
    save_interaction(_rec("c", "recebimento", user="outro"), now=now - timedelta(hours=1))
    assert save_interaction(_rec("d", "recebimento"), now=now) is None


def test_dna_evaluation_scores_and_metrics():
    save_interaction(_rec("t1", "pix"))
    ev = evaluate("t1", "msg", "Olá! Explicação. Próximo passo: acesse o portal. Até logo!", [])
    assert ev["total"] == ev["util"] + ev["calmo"] + ev["amigavel"] + ev["empoderado"]
    assert 0 <= ev["total"] <= 100
    m = metrics()
    assert m["dna_getnet"]["avaliacoes"] == 1
    assert m["assertividade"]["taxa_resolucao_sem_recontato"] == 100.0


def test_missing_next_step_lowers_calm_score():
    save_interaction(_rec("t2", "pix"))
    good = evaluate("t2", "m", "Olá! Próximo passo: acesse o portal.", [])
    bad = evaluate("t2", "m", "Depende do seu plano.", [])
    assert bad["calmo"] < good["calmo"]


def test_sensitive_data_in_response_raises_alert_even_if_judge_misses_it():
    save_interaction(_rec("t3", "pix"))
    ev = evaluate("t3", "m", "Seu CPF 123.456.789-09 está ok", [])
    assert ev["alert_dado_sensivel"] is True

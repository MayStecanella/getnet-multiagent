"""Avaliação em lote (off-line) por CSV."""
from pathlib import Path

import pytest

from app.batch_eval import BatchError, parse_csv, run_batch
from app.observability import store

EXAMPLES = Path(__file__).resolve().parents[2] / "app" / "web" / "static" / "exemplos"


def test_parse_detects_modes_and_separators():
    mode, rows = parse_csv("mensagem;triagem_esperada\nQuero pedir bobinas;adquirencia\n")
    assert mode == "replay" and rows[0]["expected_triage"] == "adquirencia"
    mode, rows = parse_csv('message,response\n"Oi","Olá! Próximo passo: acesse o portal."\n')
    assert mode == "resposta" and rows[0]["response"].startswith("Olá")


@pytest.mark.parametrize("text,error", [
    ("", "vazio"),
    ("coluna_errada\nx\n", "mensagem"),
    ("mensagem,resposta\nOi,\n", "resposta"),
])
def test_parse_errors(text, error):
    with pytest.raises(BatchError) as exc:
        parse_csv(text)
    if error:
        assert error in str(exc.value)


def test_row_limit(monkeypatch):
    from app import batch_eval
    from dataclasses import replace
    monkeypatch.setattr(batch_eval, "settings", replace(batch_eval.settings, batch_max_rows=2))
    with pytest.raises(BatchError, match="limite"):
        parse_csv("mensagem\na\nb\nc\n")


def test_replay_batch_measures_route_and_dna():
    mode, rows = parse_csv((EXAMPLES / "replay.csv").read_text(encoding="utf-8"))
    bid = store.create_batch("teste", mode, len(rows))
    run_batch(bid, mode, rows)
    b = store.get_batch(bid)
    assert b["status"] == "concluido" and b["done"] == len(rows)
    assert b["summary"]["acuracia_rota"] == 100.0
    assert b["summary"]["avaliadas"] == len(rows)
    # o lote não entra nas métricas da operação nem na fila
    assert store.metrics()["atendimentos"] == 0 and store.queue() == []


def test_response_batch_scores_specialists():
    mode, rows = parse_csv((EXAMPLES / "respostas_especialistas.csv").read_text(encoding="utf-8"))
    assert mode == "resposta"
    bid = store.create_batch("teste", mode, len(rows))
    run_batch(bid, mode, rows)
    summary = store.get_batch(bid)["summary"]
    assert set(summary["por_especialista"]) >= {"Ana", "Bruno"}
    assert summary["abaixo_de_70"] >= 1  # o exemplo tem respostas fracas de propósito


def test_random_pool_sample_runs_and_routes():
    from app.batch_eval import parse_csv
    text = (EXAMPLES / "pool_perguntas.csv").read_text(encoding="utf-8")
    mode, all_rows = parse_csv(text)
    assert mode == "replay" and len(all_rows) == 60
    assert sum(r["expected_triage"] == "web" for r in all_rows) >= 15
    _, rows = parse_csv(text, sample=10)
    assert len(rows) == 10 and len({r["message"] for r in rows}) == 10


def test_pool_sample_via_handler():
    from app.web import handlers
    text = (EXAMPLES / "pool_perguntas.csv").read_text(encoding="utf-8")
    body, tasks = handlers.batch_start({"csv": text, "name": "pool", "sample": 5}, "ip-pool")
    assert body["total"] == 5 and body["mode"] == "replay"

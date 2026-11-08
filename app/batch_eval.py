"""Avaliação em lote (off-line) a partir de um CSV.

Dois modos, detectados pelas colunas:
- replay: só a mensagem do cliente (coluna "mensagem"). Cada linha passa pelo sistema inteiro
  e a resposta é avaliada; se houver "triagem_esperada"/"agentes_esperados", mede a rota.
- resposta: mensagem + resposta já dada (colunas "mensagem" e "resposta"). Só o avaliador de
  DNA roda. Serve para avaliar atendimentos humanos já realizados (ex.: transcrições), sem
  depender do sistema estar no ar.

Colunas aceitas (português ou inglês; separador vírgula ou ponto e vírgula):
  mensagem|message, resposta|response, cliente|user_id, especialista|agent_name,
  triagem_esperada|expected_triage, agentes_esperados|expected_agents (separados por + ou |)

Uso pela linha de comando:
  python -m app.batch_eval app/web/static/exemplos/replay.csv [--out var/lote.json]
  python -m app.batch_eval app/web/static/exemplos/pool_perguntas.csv --sortear 10
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import random
import sys
from typing import Any

from app.agents.quality import evaluate
from app.config import settings
from app.observability import store

log = logging.getLogger("batch")

ALIASES = {
    "message": ("mensagem", "message", "pergunta"),
    "response": ("resposta", "response"),
    "user_id": ("cliente", "user_id", "usuario"),
    "agent_name": ("especialista", "agent_name", "atendente"),
    "expected_triage": ("triagem_esperada", "expected_triage"),
    "expected_agents": ("agentes_esperados", "expected_agents"),
}
PILLARS = ("util", "calmo", "amigavel", "empoderado")


class BatchError(ValueError):
    pass


def parse_csv(text: str, sample: int | None = None) -> tuple[str, list[dict[str, str]]]:
    """Devolve (modo, linhas normalizadas). Levanta BatchError com mensagem amigável.

    sample: sorteia essa quantidade de linhas (perguntas aleatórias de um pool).
    """
    text = text.lstrip("﻿").strip()
    if not text:
        raise BatchError("o arquivo está vazio")
    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = {h.strip().lower(): h for h in (reader.fieldnames or [])}
    columns = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in header:
                columns[key] = header[n]
                break
    if "message" not in columns:
        raise BatchError('o arquivo precisa de uma coluna "mensagem"')
    rows = []
    for raw in reader:
        row = {k: (raw.get(col) or "").strip() for k, col in columns.items()}
        if row["message"]:
            rows.append(row)
    if not rows:
        raise BatchError("nenhuma linha com mensagem")
    if sample:
        rows = random.sample(rows, min(sample, len(rows)))
    if len(rows) > settings.batch_max_rows:
        raise BatchError(f"limite de {settings.batch_max_rows} linhas por lote (o arquivo tem {len(rows)})")
    mode = "resposta" if "response" in columns else "replay"
    if mode == "resposta" and any(not r.get("response") for r in rows):
        raise BatchError('no modo resposta, todas as linhas precisam da coluna "resposta" preenchida')
    return mode, rows


def _expected_agents(value: str) -> list[str] | None:
    if not value:
        return None
    return sorted(a.strip() for a in value.replace("|", "+").split("+") if a.strip())


def evaluate_row(i: int, row: dict[str, str], mode: str) -> dict[str, Any]:
    from app.service import handle_message  # import tardio: evita ciclo com o grafo

    out: dict[str, Any] = {"linha": i + 1, "mensagem": row["message"]}
    if mode == "replay":
        r = handle_message(row["message"], row.get("user_id") or "cliente1988", channel="batch")
        out.update(resposta=r.response, triagem=r.triage, agentes=r.agents, tema=r.theme, tools=r.tools_used)
        expected_triage = row.get("expected_triage") or None
        expected_agents = _expected_agents(row.get("expected_agents", ""))
        if expected_triage or expected_agents is not None:
            ok = True
            if expected_triage:
                ok &= r.triage == expected_triage
            if expected_agents is not None:
                ok &= sorted(r.agents) == expected_agents
            out["rota_ok"] = ok
        ev = None if r.error else evaluate(r.trace_id, r.masked_message, r.response, r.sources, save=False)
    else:
        out.update(resposta=row["response"], especialista=row.get("agent_name") or None)
        ev = evaluate(f"lote-{i}", row["message"], row["response"], [], author="humano",
                      agent_name=row.get("agent_name") or None, save=False)
    out["dna"] = ev
    return out


def summarize(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    scored = [r["dna"] for r in rows if r.get("dna")]
    n = len(scored)
    summary: dict[str, Any] = {
        "modo": mode,
        "linhas": len(rows),
        "avaliadas": n,
        "dna_medio": round(sum(e["total"] for e in scored) / n, 1) if n else None,
        "por_pilar": {p: round(sum(e[p] for e in scored) / n, 1) if n else None for p in PILLARS},
        "abaixo_de_70": sum(e["total"] < 70 for e in scored),
        "alertas_sem_fonte": sum(bool(e["alert_sem_fonte"]) for e in scored),
        "alertas_dado_sensivel": sum(bool(e["alert_dado_sensivel"]) for e in scored),
    }
    with_route = [r for r in rows if "rota_ok" in r]
    if with_route:
        summary["acuracia_rota"] = round(100 * sum(r["rota_ok"] for r in with_route) / len(with_route), 1)
    by_agent: dict[str, list[int]] = {}
    for r in rows:
        if r.get("especialista") and r.get("dna"):
            by_agent.setdefault(r["especialista"], []).append(r["dna"]["total"])
    if by_agent:
        summary["por_especialista"] = {k: round(sum(v) / len(v), 1) for k, v in sorted(by_agent.items())}
    return summary


def run_batch(batch_id: str, mode: str, rows: list[dict[str, str]]) -> None:
    """Executa o lote linha a linha, gravando o progresso (usado em segundo plano pela API)."""
    results: list[dict[str, Any]] = []
    try:
        for i, row in enumerate(rows):
            try:
                results.append(evaluate_row(i, row, mode))
            except Exception as exc:  # uma linha com falha não derruba o lote
                log.exception("falha na linha %d do lote", i + 1)
                results.append({"linha": i + 1, "mensagem": row["message"], "erro": str(exc), "dna": None})
            store.update_batch(batch_id, done=i + 1)
        store.update_batch(batch_id, status="concluido", rows=results, summary=summarize(results, mode))
    except Exception as exc:
        log.exception("falha no lote %s", batch_id)
        store.update_batch(batch_id, status="erro", error=str(exc), rows=results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Avaliação em lote a partir de um CSV")
    parser.add_argument("csv", help="caminho do arquivo CSV")
    parser.add_argument("--out", default=str(settings.data_dir / "lote.json"))
    parser.add_argument("--sortear", type=int, default=None, metavar="N",
                        help="sorteia N perguntas do arquivo (ex.: pool_perguntas.csv)")
    args = parser.parse_args()
    try:
        with open(args.csv, encoding="utf-8-sig") as f:
            mode, rows = parse_csv(f.read(), sample=args.sortear)
    except (OSError, BatchError) as exc:
        print(f"Erro: {exc}")
        return 1
    bid = store.create_batch(args.csv, mode, len(rows))
    print(f"Lote {bid}: {len(rows)} linhas, modo {mode}")
    run_batch(bid, mode, rows)
    result = store.get_batch(bid)
    print("| # | Mensagem | Rota ok | Útil | Calmo | Amigável | Empoderado | Total |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in result["rows"]:
        d = r.get("dna") or {}
        rota = {True: "sim", False: "NÃO"}.get(r.get("rota_ok"), "-")
        print(f"| {r['linha']} | {r['mensagem'][:55]} | {rota} | {d.get('util', '-')} | {d.get('calmo', '-')} | "
              f"{d.get('amigavel', '-')} | {d.get('empoderado', '-')} | {d.get('total', '-')} |")
    print("\nResumo:", json.dumps(result["summary"], ensure_ascii=False))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Relatório completo: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

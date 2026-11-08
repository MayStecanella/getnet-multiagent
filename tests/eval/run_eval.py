"""Avaliação com LLM real: roda os cenários, mede roteamento e DNA Getnet e simula recontato.

Uso (dentro do container ou com o .env configurado):
    python -m tests.eval.run_eval [--min-dna 70]

Gera var/eval_report.json e imprime uma tabela em Markdown (para o README).
Sai com código 1 se a acurácia de rota for < 100% ou o DNA médio ficar abaixo do mínimo,
o que permite usar como teste de regressão de prompts.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.agents.quality import evaluate
from app.config import settings
from app.observability.store import get_interaction, metrics
from app.service import handle_message
from tests.scenarios import SCENARIOS, agents_match

# retornos simulados do mesmo cliente no mesmo tema, para medir recontato
RECONTACT_PAIRS = [
    ("When will the money from yesterday's sales be deposited?", "O dinheiro de ontem ainda não caiu, quando vai cair?"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-dna", type=float, default=70)
    args = parser.parse_args()

    rows, route_hits = [], 0
    for message, triage, agents, tools in SCENARIOS:
        r = handle_message(message, "cliente1988", channel="eval")
        ev = evaluate(r.trace_id, r.masked_message, r.response, r.sources, save=False) if not r.error else None
        ok = r.triage == triage and agents_match(r.agents, agents) and all(t in r.tools_used for t in tools)
        route_hits += ok
        rows.append({"message": message, "expected": [triage, agents], "got": [r.triage, r.agents],
                     "route_ok": ok, "tools": r.tools_used, "dna": ev, "response": r.response})

    recontacts = []
    for first, second in RECONTACT_PAIRS:
        a = handle_message(first, "cliente1988", channel="eval")
        handle_message(second, "cliente1988", channel="eval")
        recontacts.append({"first": first, "second": second,
                           "detected": bool(get_interaction(a.trace_id)["recontacted"])})

    scored = [row["dna"]["total"] for row in rows if row["dna"]]
    report = {
        "route_accuracy": round(100 * route_hits / len(SCENARIOS), 1),
        "dna_mean": round(sum(scored) / len(scored), 1) if scored else None,
        "recontact_detection": recontacts,
        "metrics": metrics(),
        "rows": rows,
    }
    out = settings.data_dir / "eval_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print("| Mensagem | Rota ok | Útil | Calmo | Amigável | Empoderado | Total |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        d = row["dna"] or {}
        print(f"| {row['message'][:60]} | {'sim' if row['route_ok'] else 'NÃO'} | {d.get('util', '-')} | "
              f"{d.get('calmo', '-')} | {d.get('amigavel', '-')} | {d.get('empoderado', '-')} | {d.get('total', '-')} |")
    print(f"\nAcurácia de rota: {report['route_accuracy']}% · DNA médio: {report['dna_mean']} · "
          f"Recontato detectado: {all(x['detected'] for x in recontacts)}")
    print(f"Relatório completo: {out}")

    failed = report["route_accuracy"] < 100 or (report["dna_mean"] or 0) < args.min_dna
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

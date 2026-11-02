"""Registro de atendimentos e avaliações (SQLite) + regra de recontato + métricas."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    trace_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message_masked TEXT NOT NULL,
    triage TEXT NOT NULL,
    routes TEXT NOT NULL,
    theme TEXT,
    response TEXT NOT NULL,
    sources TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    recontacted INTEGER NOT NULL DEFAULT 0,    -- 1 = cliente voltou no mesmo tema (não resolvido)
    recontact_trace_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_interactions_user_theme ON interactions(user_id, theme, created_at);
CREATE TABLE IF NOT EXISTS evaluations (
    trace_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    util INTEGER NOT NULL,
    calmo INTEGER NOT NULL,
    amigavel INTEGER NOT NULL,
    empoderado INTEGER NOT NULL,
    total INTEGER NOT NULL,
    justifications TEXT NOT NULL,
    alert_sem_fonte INTEGER NOT NULL,
    alert_dado_sensivel INTEGER NOT NULL
);
"""

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_interaction(record: dict[str, Any], now: datetime | None = None) -> str | None:
    """Grava o atendimento e aplica a regra de recontato.

    Se o mesmo user_id já teve atendimento no mesmo tema dentro da janela, o anterior
    é marcado como não resolvido. Devolve o trace_id do atendimento marcado, se houver.
    """
    now = now or _now()
    window_start = (now - timedelta(hours=settings.recontact_window_hours)).isoformat()
    with _lock, _connect() as conn:
        previous = None
        if record.get("theme"):
            previous = conn.execute(
                "SELECT trace_id FROM interactions WHERE user_id = ? AND theme = ? AND created_at >= ? "
                "AND recontacted = 0 ORDER BY created_at DESC LIMIT 1",
                (record["user_id"], record["theme"], window_start),
            ).fetchone()
            if previous:
                conn.execute(
                    "UPDATE interactions SET recontacted = 1, recontact_trace_id = ? WHERE trace_id = ?",
                    (record["trace_id"], previous["trace_id"]),
                )
        conn.execute(
            "INSERT INTO interactions (trace_id, user_id, created_at, message_masked, triage, routes, theme, "
            "response, sources, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                record["trace_id"], record["user_id"], now.isoformat(), record["message_masked"],
                record["triage"], json.dumps(record.get("routes", [])), record.get("theme"),
                record["response"], json.dumps(record.get("sources", [])), record["latency_ms"],
            ),
        )
    return previous["trace_id"] if previous else None


def save_evaluation(trace_id: str, ev: dict[str, Any]) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                trace_id, _now().isoformat(), ev["util"], ev["calmo"], ev["amigavel"], ev["empoderado"],
                ev["total"], json.dumps(ev["justifications"], ensure_ascii=False),
                int(ev["alert_sem_fonte"]), int(ev["alert_dado_sensivel"]),
            ),
        )


def get_interaction(trace_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM interactions WHERE trace_id = ?", (trace_id,)).fetchone()
    return dict(row) if row else None


def metrics() -> dict[str, Any]:
    """Resumo para o endpoint /metrics."""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        triage = {r[0]: r[1] for r in conn.execute("SELECT triage, COUNT(*) FROM interactions GROUP BY triage")}
        latency = conn.execute("SELECT AVG(latency_ms) FROM interactions").fetchone()[0]

        themed = conn.execute("SELECT COUNT(*), SUM(recontacted) FROM interactions WHERE theme IS NOT NULL").fetchone()
        by_theme = [
            dict(r) for r in conn.execute(
                "SELECT theme, COUNT(*) AS atendimentos, SUM(recontacted) AS recontatos FROM interactions "
                "WHERE theme IS NOT NULL GROUP BY theme ORDER BY atendimentos DESC"
            )
        ]
        dna = conn.execute(
            "SELECT COUNT(*), AVG(util), AVG(calmo), AVG(amigavel), AVG(empoderado), AVG(total), "
            "SUM(alert_sem_fonte), SUM(alert_dado_sensivel) FROM evaluations"
        ).fetchone()
        by_agent = [
            dict(r) for r in conn.execute(
                "SELECT i.routes AS rota, COUNT(*) AS avaliacoes, ROUND(AVG(e.total), 1) AS dna_medio "
                "FROM evaluations e JOIN interactions i USING (trace_id) GROUP BY i.routes"
            )
        ]

    def r(x):
        return round(x, 1) if x is not None else None

    themed_total, recontacts = themed[0], themed[1] or 0
    return {
        "atendimentos": total,
        "triagem": triage,
        "latencia_media_ms": r(latency),
        "assertividade": {
            "atendimentos_com_tema": themed_total,
            "recontatos": recontacts,
            "taxa_resolucao_sem_recontato": r(100 * (1 - recontacts / themed_total)) if themed_total else None,
            "janela_horas": settings.recontact_window_hours,
            "por_tema": by_theme,
        },
        "dna_getnet": {
            "avaliacoes": dna[0],
            "media_util": r(dna[1]),
            "media_calmo": r(dna[2]),
            "media_amigavel": r(dna[3]),
            "media_empoderado": r(dna[4]),
            "media_total": r(dna[5]),
            "alertas_sem_fonte": dna[6] or 0,
            "alertas_dado_sensivel": dna[7] or 0,
            "por_rota": by_agent,
        },
    }

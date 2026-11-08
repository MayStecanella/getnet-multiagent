"""Persistência (SQLite): conversas, mensagens, atendimentos da IA, avaliações e lotes.

- conversations / messages: o fio da conversa, inclusive o transbordo para o especialista.
- interactions: um registro por resposta da IA (triagem, rota, tema, fontes, latência) e a
  regra de recontato.
- evaluations: notas de DNA Getnet, tanto da IA quanto dos especialistas humanos.
- batch_runs: avaliações em lote (off-line) disparadas pela tela da qualidade ou pela CLI.

Canais internos (lote e avaliação) ficam fora da fila do especialista e das métricas de operação.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings

INTERNAL_CHANNELS = {"batch", "eval"}

# status da conversa
IA = "ia"
WAITING = "aguardando_humano"
WITH_HUMAN = "em_atendimento"
CLOSED = "encerrada"
HUMAN_STATUSES = {WAITING, WITH_HUMAN}

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    internal INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    handoff_reason TEXT,
    handoff_at TEXT,
    handoff_theme TEXT,
    ai_suggestion TEXT,
    assigned_agent TEXT,
    claimed_at TEXT,
    closed_at TEXT,
    closed_by TEXT,
    awaiting_feedback TEXT             -- trace_id da resposta da IA que perguntou "resolveu?"
);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status, internal);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL,              -- cliente | ia | humano | sistema
    author_name TEXT,
    text TEXT NOT NULL,
    trace_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS interactions (
    trace_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'api',
    internal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    message_masked TEXT NOT NULL,
    triage TEXT NOT NULL,
    routes TEXT NOT NULL,
    theme TEXT,
    response TEXT NOT NULL,
    sources TEXT NOT NULL,
    tools TEXT NOT NULL DEFAULT '[]',
    latency_ms INTEGER NOT NULL,
    recontacted INTEGER NOT NULL DEFAULT 0,    -- 1 = cliente voltou no mesmo tema (não resolvido)
    recontact_trace_id TEXT,
    asked_feedback INTEGER NOT NULL DEFAULT 0, -- 1 = a resposta perguntou "consegui resolver?"
    feedback INTEGER                           -- 1 = cliente disse que resolveu, 0 = não resolveu
);
CREATE INDEX IF NOT EXISTS idx_interactions_user_theme ON interactions(user_id, theme, created_at);
CREATE INDEX IF NOT EXISTS idx_interactions_conversation ON interactions(conversation_id);
CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL,              -- ia | humano
    agent_name TEXT,
    conversation_id TEXT,
    trace_id TEXT,
    message TEXT,
    response TEXT,
    util INTEGER NOT NULL,
    calmo INTEGER NOT NULL,
    amigavel INTEGER NOT NULL,
    empoderado INTEGER NOT NULL,
    total INTEGER NOT NULL,
    justifications TEXT NOT NULL,
    alert_sem_fonte INTEGER NOT NULL,
    alert_dado_sensivel INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,                -- replay | resposta
    status TEXT NOT NULL,              -- executando | concluido | erro
    total INTEGER NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    rows TEXT,
    error TEXT
);
"""

_lock = threading.Lock()
_migrated: set[str] = set()

# colunas acrescentadas depois da primeira versão do banco (bancos antigos são atualizados)
MIGRATIONS = [
    ("conversations", "closed_by", "TEXT"),
    ("conversations", "awaiting_feedback", "TEXT"),
    ("interactions", "asked_feedback", "INTEGER NOT NULL DEFAULT 0"),
    ("interactions", "feedback", "INTEGER"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    key = str(settings.db_path)
    if key in _migrated:
        return
    for table, column, ddl in MIGRATIONS:
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _migrated.add(key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


@contextmanager
def _db():
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _is_internal(channel: str) -> int:
    return int(channel in INTERNAL_CHANNELS)


# --- conversas -------------------------------------------------------------
def create_conversation(user_id: str, channel: str = "api") -> dict:
    cid = uuid.uuid4().hex[:16]
    now = _iso()
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, user_id, channel, internal, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, user_id, channel, _is_internal(channel), IA, now, now),
        )
    return get_conversation(cid)


def get_conversation(cid: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def add_message(cid: str, author: str, text: str, author_name: str | None = None,
                trace_id: str | None = None) -> int:
    now = _iso()
    with _lock, _db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, created_at, author, author_name, text, trace_id) "
            "VALUES (?,?,?,?,?,?)",
            (cid, now, author, author_name, text, trace_id),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))
        return int(cur.lastrowid)


def list_messages(cid: str, after_id: int = 0) -> list[dict]:
    """Mensagens da conversa; as da IA trazem os bastidores (triagem, rota, tema, fontes)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT m.*, i.triage, i.routes, i.theme, i.sources, i.tools FROM messages m "
            "LEFT JOIN interactions i ON i.trace_id = m.trace_id "
            "WHERE m.conversation_id = ? AND m.id > ? ORDER BY m.id",
            (cid, after_id),
        ).fetchall()
    out = []
    for r in rows:
        msg = {k: r[k] for k in ("id", "created_at", "author", "author_name", "text", "trace_id")}
        if r["triage"] is not None:
            msg["meta"] = {
                "triage": r["triage"], "agents": json.loads(r["routes"]), "theme": r["theme"],
                "sources": json.loads(r["sources"]), "tools": json.loads(r["tools"] or "[]"),
            }
        out.append(msg)
    return out


def conversation_themes(cid: str) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT theme FROM interactions WHERE conversation_id = ? AND triage = 'adquirencia' "
            "AND theme IS NOT NULL ORDER BY created_at", (cid,),
        ).fetchall()
    return [r["theme"] for r in rows]


def set_handoff(cid: str, reason: str, theme: str | None = None, suggestion: str | None = None) -> None:
    now = _iso()
    with _lock, _db() as conn:
        conn.execute(
            "UPDATE conversations SET status = ?, handoff_reason = ?, handoff_at = ?, handoff_theme = ?, "
            "ai_suggestion = ?, awaiting_feedback = NULL, updated_at = ? WHERE id = ?",
            (WAITING, reason, now, theme, suggestion, now, cid),
        )


def set_awaiting_feedback(cid: str, trace_id: str | None) -> None:
    with _lock, _db() as conn:
        conn.execute("UPDATE conversations SET awaiting_feedback = ? WHERE id = ?", (trace_id, cid))


def record_feedback(trace_id: str, resolved: bool) -> None:
    with _lock, _db() as conn:
        conn.execute("UPDATE interactions SET feedback = ? WHERE trace_id = ?", (int(resolved), trace_id))


def close_by_client(cid: str) -> None:
    """Cliente confirmou que o pedido foi resolvido: a conversa termina."""
    now = _iso()
    with _lock, _db() as conn:
        conn.execute(
            "UPDATE conversations SET status = ?, closed_at = ?, closed_by = 'cliente', awaiting_feedback = NULL, "
            "updated_at = ? WHERE id = ?", (CLOSED, now, now, cid),
        )


def claim_conversation(cid: str, agent_name: str) -> dict:
    """Especialista assume a conversa. Só assume se estiver aguardando ou já for dele."""
    now = _iso()
    with _lock, _db() as conn:
        row = conn.execute("SELECT status, assigned_agent FROM conversations WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise LookupError("conversa não encontrada")
        if row["status"] == WITH_HUMAN and row["assigned_agent"] != agent_name:
            raise PermissionError(f"conversa já está com {row['assigned_agent']}")
        if row["status"] not in HUMAN_STATUSES:
            raise PermissionError("conversa não está na fila do especialista")
        if row["status"] == WAITING:
            conn.execute(
                "UPDATE conversations SET status = ?, assigned_agent = ?, claimed_at = ?, updated_at = ? "
                "WHERE id = ?", (WITH_HUMAN, agent_name, now, now, cid),
            )
    return get_conversation(cid)


def close_conversation(cid: str, agent_name: str) -> dict:
    now = _iso()
    with _lock, _db() as conn:
        row = conn.execute("SELECT status, assigned_agent FROM conversations WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise LookupError("conversa não encontrada")
        if row["status"] != WITH_HUMAN or row["assigned_agent"] != agent_name:
            raise PermissionError("só quem assumiu a conversa pode encerrá-la")
        conn.execute("UPDATE conversations SET status = ?, closed_at = ?, closed_by = ?, updated_at = ? "
                     "WHERE id = ?", (CLOSED, now, agent_name, now, cid))
    return get_conversation(cid)


def queue() -> list[dict]:
    """Fila do especialista: conversas aguardando ou em atendimento (canais externos)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT c.*, "
            " (SELECT text FROM messages m WHERE m.conversation_id = c.id AND m.author = 'cliente' "
            "  ORDER BY m.id DESC LIMIT 1) AS last_client_message, "
            " (SELECT author FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) "
            "  AS last_author "
            "FROM conversations c WHERE c.status IN (?, ?) AND c.internal = 0 ORDER BY c.handoff_at",
            (WAITING, WITH_HUMAN),
        ).fetchall()
    return [dict(r) for r in rows]


def conversation_context(cid: str) -> dict:
    """O que a IA já fez na conversa: últimos temas, rotas, fontes e tools."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT triage, routes, theme, sources, tools FROM interactions WHERE conversation_id = ? "
            "ORDER BY created_at", (cid,),
        ).fetchall()
    themes, agents, sources, tools = [], [], [], []
    for r in rows:
        if r["theme"] and r["theme"] not in themes:
            themes.append(r["theme"])
        for key, bucket in (("routes", agents), ("sources", sources), ("tools", tools)):
            for item in json.loads(r[key] or "[]"):
                if item not in bucket:
                    bucket.append(item)
    return {"themes": themes, "agents": agents, "sources": sources, "tools": tools}


# --- atendimentos da IA e recontato -----------------------------------------
def save_interaction(record: dict[str, Any], now: datetime | None = None) -> str | None:
    """Grava o atendimento e aplica a regra de recontato.

    Se o mesmo user_id já teve atendimento no mesmo tema dentro da janela, o anterior
    é marcado como não resolvido. Devolve o trace_id do atendimento marcado, se houver.
    Canais internos só se comparam entre si, para não contaminar a operação.
    """
    now = now or _now()
    channel = record.get("channel", "api")
    internal = _is_internal(channel)
    window_start = (now - timedelta(hours=settings.recontact_window_hours)).isoformat()
    with _lock, _db() as conn:
        previous = None
        if record.get("theme"):
            previous = conn.execute(
                "SELECT trace_id FROM interactions WHERE user_id = ? AND theme = ? AND created_at >= ? "
                "AND recontacted = 0 AND internal = ? ORDER BY created_at DESC LIMIT 1",
                (record["user_id"], record["theme"], window_start, internal),
            ).fetchone()
            if previous:
                conn.execute(
                    "UPDATE interactions SET recontacted = 1, recontact_trace_id = ? WHERE trace_id = ?",
                    (record["trace_id"], previous["trace_id"]),
                )
        conn.execute(
            "INSERT INTO interactions (trace_id, conversation_id, user_id, channel, internal, created_at, "
            "message_masked, triage, routes, theme, response, sources, tools, latency_ms, asked_feedback) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record["trace_id"], record.get("conversation_id"), record["user_id"], channel, internal,
                now.isoformat(), record["message_masked"], record["triage"],
                json.dumps(record.get("routes", [])), record.get("theme"), record["response"],
                json.dumps(record.get("sources", [])), json.dumps(record.get("tools", [])),
                record["latency_ms"], int(bool(record.get("asked_feedback"))),
            ),
        )
    return previous["trace_id"] if previous else None


def get_interaction(trace_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM interactions WHERE trace_id = ?", (trace_id,)).fetchone()
    return dict(row) if row else None


# --- avaliações de DNA -------------------------------------------------------
def save_evaluation(eval_id: str, ev: dict[str, Any], *, author: str = "ia", agent_name: str | None = None,
                    conversation_id: str | None = None, trace_id: str | None = None,
                    message: str | None = None, response: str | None = None) -> None:
    with _lock, _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eval_id, _iso(), author, agent_name, conversation_id, trace_id, message, response,
                ev["util"], ev["calmo"], ev["amigavel"], ev["empoderado"], ev["total"],
                json.dumps(ev["justifications"], ensure_ascii=False),
                int(ev["alert_sem_fonte"]), int(ev["alert_dado_sensivel"]),
            ),
        )


def list_evaluations(author: str | None = None, max_total: int | None = None,
                     conversation_id: str | None = None, agent_name: str | None = None,
                     limit: int = 50) -> list[dict]:
    query, params = "SELECT * FROM evaluations WHERE 1=1", []
    for col, val in (("author", author), ("conversation_id", conversation_id), ("agent_name", agent_name)):
        if val:
            query += f" AND {col} = ?"
            params.append(val)
    if max_total is not None:
        query += " AND total < ?"
        params.append(max_total)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _db() as conn:
        rows = conn.execute(query, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["justifications"] = json.loads(d["justifications"])
        d["alert_sem_fonte"] = bool(d["alert_sem_fonte"])
        d["alert_dado_sensivel"] = bool(d["alert_dado_sensivel"])
        out.append(d)
    return out


# --- avaliação em lote --------------------------------------------------------
def create_batch(name: str, mode: str, total: int) -> str:
    bid = uuid.uuid4().hex[:12]
    with _lock, _db() as conn:
        conn.execute("INSERT INTO batch_runs (id, created_at, name, mode, status, total) VALUES (?,?,?,?,?,?)",
                     (bid, _iso(), name, mode, "executando", total))
    return bid


def update_batch(bid: str, **fields) -> None:
    if not fields:
        return
    for key in ("summary", "rows"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _db() as conn:
        conn.execute(f"UPDATE batch_runs SET {sets} WHERE id = ?", (*fields.values(), bid))


def get_batch(bid: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM batch_runs WHERE id = ?", (bid,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["summary"] = json.loads(d["summary"]) if d["summary"] else None
    d["rows"] = json.loads(d["rows"]) if d["rows"] else []
    return d


def list_batches(limit: int = 20) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, created_at, name, mode, status, total, done, summary FROM batch_runs "
            "ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d["summary"]) if d["summary"] else None
        out.append(d)
    return out


# --- métricas ------------------------------------------------------------------
def _r(x):
    return round(x, 1) if x is not None else None


def _dna_block(conn, where: str, params: tuple) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), AVG(util), AVG(calmo), AVG(amigavel), AVG(empoderado), AVG(total), "
        f"SUM(alert_sem_fonte), SUM(alert_dado_sensivel), SUM(total < 70) FROM evaluations WHERE {where}",
        params,
    ).fetchone()
    return {
        "avaliacoes": row[0], "media_util": _r(row[1]), "media_calmo": _r(row[2]),
        "media_amigavel": _r(row[3]), "media_empoderado": _r(row[4]), "media_total": _r(row[5]),
        "alertas_sem_fonte": row[6] or 0, "alertas_dado_sensivel": row[7] or 0, "abaixo_de_70": row[8] or 0,
    }


def metrics() -> dict[str, Any]:
    """Resumo para a tela da qualidade e o endpoint /metrics (só canais externos)."""
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM interactions WHERE internal = 0").fetchone()[0]
        triage = {r[0]: r[1] for r in conn.execute(
            "SELECT triage, COUNT(*) FROM interactions WHERE internal = 0 GROUP BY triage")}
        latency = conn.execute("SELECT AVG(latency_ms) FROM interactions WHERE internal = 0").fetchone()[0]

        themed = conn.execute(
            "SELECT COUNT(*), SUM(recontacted) FROM interactions WHERE theme IS NOT NULL AND internal = 0"
        ).fetchone()
        by_theme = [dict(r) for r in conn.execute(
            "SELECT theme, COUNT(*) AS atendimentos, SUM(recontacted) AS recontatos FROM interactions "
            "WHERE theme IS NOT NULL AND internal = 0 GROUP BY theme ORDER BY atendimentos DESC")]

        fb = conn.execute(
            "SELECT SUM(asked_feedback), COUNT(feedback), SUM(feedback = 1) FROM interactions WHERE internal = 0"
        ).fetchone()
        conv = conn.execute(
            "SELECT COUNT(*), SUM(handoff_at IS NOT NULL), SUM(status = ?), SUM(status = ?), "
            "AVG(CASE WHEN claimed_at IS NOT NULL THEN (julianday(claimed_at) - julianday(handoff_at)) * 86400 END) "
            "FROM conversations WHERE internal = 0", (WAITING, WITH_HUMAN),
        ).fetchone()
        reasons = {r[0]: r[1] for r in conn.execute(
            "SELECT handoff_reason, COUNT(*) FROM conversations WHERE internal = 0 AND handoff_reason IS NOT NULL "
            "GROUP BY handoff_reason")}

        dna_all = _dna_block(conn, "1=1", ())
        dna_ia = _dna_block(conn, "author = ?", ("ia",))
        dna_human = _dna_block(conn, "author = ?", ("humano",))
        by_agent = [dict(r) for r in conn.execute(
            "SELECT agent_name AS especialista, COUNT(*) AS avaliacoes, ROUND(AVG(total), 1) AS dna_medio, "
            "ROUND(AVG(util), 1) AS util, ROUND(AVG(calmo), 1) AS calmo, ROUND(AVG(amigavel), 1) AS amigavel, "
            "ROUND(AVG(empoderado), 1) AS empoderado FROM evaluations WHERE author = 'humano' "
            "GROUP BY agent_name ORDER BY dna_medio DESC")]
        by_route = [dict(r) for r in conn.execute(
            "SELECT i.routes AS rota, COUNT(*) AS avaliacoes, ROUND(AVG(e.total), 1) AS dna_medio "
            "FROM evaluations e JOIN interactions i ON i.trace_id = e.trace_id WHERE e.author = 'ia' "
            "GROUP BY i.routes")]

    asked, answered, resolved = fb[0] or 0, fb[1] or 0, fb[2] or 0
    themed_total, recontacts = themed[0], themed[1] or 0
    conversations, handoffs = conv[0], conv[1] or 0
    return {
        "atendimentos": total,
        "triagem": triage,
        "latencia_media_ms": _r(latency),
        "assertividade": {
            "atendimentos_com_tema": themed_total,
            "recontatos": recontacts,
            "taxa_resolucao_sem_recontato": _r(100 * (1 - recontacts / themed_total)) if themed_total else None,
            "janela_horas": settings.recontact_window_hours,
            "por_tema": by_theme,
        },
        "resolucao": {
            "perguntas": asked,
            "respondidas": answered,
            "resolvidas": resolved,
            "nao_resolvidas": answered - resolved,
            "taxa_resolucao_confirmada": _r(100 * resolved / answered) if answered else None,
            "taxa_resposta": _r(100 * answered / asked) if asked else None,
        },
        "transbordo": {
            "conversas": conversations,
            "transbordadas": handoffs,
            "taxa_transbordo": _r(100 * handoffs / conversations) if conversations else None,
            "na_fila": conv[2] or 0,
            "em_atendimento": conv[3] or 0,
            "espera_media_s": _r(conv[4]),
            "por_motivo": reasons,
        },
        "dna_getnet": {**dna_all, "ia": dna_ia, "humano": dna_human,
                       "por_especialista": by_agent, "por_rota": by_route},
    }

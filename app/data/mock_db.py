"""Base mock da conta do cliente (SQLite).

Os dados são gerados com datas relativas a hoje, para que "as vendas de ontem" sempre
existam nos cenários de teste. Não há CPF, CNPJ nem endereço na base, por coerência
com a regra de dados sensíveis.

Uso: python -m app.data.mock_db
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchants (
    user_id TEXT PRIMARY KEY,
    trade_name TEXT NOT NULL,
    settlement_plan TEXT NOT NULL,      -- descrição do plano de recebimento
    settlement_days_debit INTEGER NOT NULL,
    settlement_days_credit INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    product TEXT NOT NULL,              -- debito | credito_a_vista | credito_parcelado | pix
    installments INTEGER NOT NULL,
    amount REAL NOT NULL,
    fee_rate REAL NOT NULL,
    status TEXT NOT NULL,               -- aprovada | recusada
    decline_code TEXT,
    decline_reason TEXT,
    terminal_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminals (
    terminal_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    connection TEXT NOT NULL,
    status TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    note TEXT
);
"""


def _db_path() -> Path:
    return settings.data_dir / "mock.db"


def connect() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        seed()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def seed(today: date | None = None) -> Path:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executescript("DELETE FROM merchants; DELETE FROM transactions; DELETE FROM terminals;")

    conn.execute(
        "INSERT INTO merchants VALUES (?,?,?,?,?)",
        ("cliente1988", "Padaria Exemplo", "Débito em 1 dia útil e crédito em 30 dias", 1, 30),
    )

    def ts(d: date, hh: int, mm: int) -> str:
        return datetime(d.year, d.month, d.day, hh, mm).isoformat()

    rows = [
        ("t1", ts(yesterday, 9, 12), "debito", 1, 45.90, 0.0199, "aprovada", None, None, "T-001"),
        ("t2", ts(yesterday, 11, 40), "credito_a_vista", 1, 120.00, 0.0349, "aprovada", None, None, "T-001"),
        ("t3", ts(yesterday, 15, 5), "debito", 1, 32.50, 0.0199, "aprovada", None, None, "T-001"),
        ("t4", ts(yesterday, 17, 30), "credito_parcelado", 3, 300.00, 0.0499, "aprovada", None, None, "T-001"),
        ("t5", ts(yesterday, 18, 2), "pix", 1, 58.00, 0.0, "aprovada", None, None, "T-001"),
        ("t6", ts(today, 8, 47), "credito_a_vista", 1, 89.90, 0.0349, "recusada", "51",
         "Saldo ou limite insuficiente no cartão do comprador", "T-001"),
        ("t7", ts(today, 9, 15), "debito", 1, 27.00, 0.0199, "recusada", "55",
         "Senha digitada incorretamente pelo comprador", "T-001"),
    ]
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(r[0], "cliente1988", *r[1:]) for r in rows],
    )
    conn.execute(
        "INSERT INTO terminals VALUES (?,?,?,?,?,?,?)",
        ("T-001", "cliente1988", "Get Smart", "Wi-Fi", "conexao_instavel",
         ts(today, 7, 58), "Sinal de Wi-Fi fraco nas últimas tentativas de comunicação"),
    )
    conn.commit()
    conn.close()
    return path


if __name__ == "__main__":
    print(f"Base mock criada em {seed()}")

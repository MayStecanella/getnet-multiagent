"""Tools do Customer Support: só leitura e sempre filtradas pelo user_id da requisição."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.data.mock_db import connect


def _add_business_days(d: date, days: int) -> date:
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def _merchant(conn, user_id: str):
    return conn.execute("SELECT * FROM merchants WHERE user_id = ?", (user_id,)).fetchone()


def get_settlements(user_id: str, sales_date: str | None = None) -> dict[str, Any]:
    """Recebimentos previstos das vendas aprovadas de uma data (padrão: ontem)."""
    day = date.fromisoformat(sales_date) if sales_date else date.today() - timedelta(days=1)
    with connect() as conn:
        merchant = _merchant(conn, user_id)
        if merchant is None:
            return {"found": False, "reason": "cliente não encontrado"}
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id = ? AND status = 'aprovada' "
            "AND substr(created_at, 1, 10) = ?",
            (user_id, day.isoformat()),
        ).fetchall()

    items = []
    for r in rows:
        if r["product"] in ("debito", "pix"):
            installments = [(_add_business_days(day, merchant["settlement_days_debit"]) if r["product"] == "debito" else day, r["amount"])]
        else:
            n = r["installments"]
            per = r["amount"] / n
            installments = [(day + timedelta(days=merchant["settlement_days_credit"] * (i + 1)), per) for i in range(n)]
        for i, (when, gross) in enumerate(installments, start=1):
            fee = round(gross * r["fee_rate"], 2)
            items.append({
                "transaction_id": r["id"],
                "product": r["product"],
                "installment": f"{i}/{len(installments)}",
                "expected_date": when.isoformat(),
                "gross": round(gross, 2),
                "fee": fee,
                "net": round(gross - fee, 2),
            })

    by_date: dict[str, float] = {}
    for it in items:
        by_date[it["expected_date"]] = round(by_date.get(it["expected_date"], 0) + it["net"], 2)

    return {
        "found": True,
        "sales_date": day.isoformat(),
        "settlement_plan": merchant["settlement_plan"],
        "net_by_date": dict(sorted(by_date.items())),
        "items": items,
        # Regras de liquidação simuladas da base mock: Pix no mesmo dia, débito em dias
        # úteis conforme o plano, crédito parcelado com uma parcela a cada ciclo do plano.
        "note": "Prazos calculados pelo plano de recebimento do cliente.",
    }


def get_transactions(user_id: str, status: str = "todas", days: int = 7) -> dict[str, Any]:
    """Transações recentes, com código e motivo quando recusadas."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    query = "SELECT * FROM transactions WHERE user_id = ? AND created_at >= ?"
    params: list[Any] = [user_id, since]
    if status in ("aprovada", "recusada"):
        query += " AND status = ?"
        params.append(status)
    with connect() as conn:
        if _merchant(conn, user_id) is None:
            return {"found": False, "reason": "cliente não encontrado"}
        rows = conn.execute(query + " ORDER BY created_at DESC", params).fetchall()
    return {
        "found": True,
        "transactions": [
            {
                "id": r["id"], "created_at": r["created_at"], "product": r["product"],
                "amount": r["amount"], "status": r["status"],
                "decline_code": r["decline_code"], "decline_reason": r["decline_reason"],
                "terminal_id": r["terminal_id"],
            }
            for r in rows
        ],
    }


def get_terminal_status(user_id: str) -> dict[str, Any]:
    """Status de conexão das máquinas do cliente."""
    with connect() as conn:
        if _merchant(conn, user_id) is None:
            return {"found": False, "reason": "cliente não encontrado"}
        rows = conn.execute("SELECT * FROM terminals WHERE user_id = ?", (user_id,)).fetchall()
    return {"found": True, "terminals": [dict(r) for r in rows]}


TOOLS = {
    "get_settlements": get_settlements,
    "get_transactions": get_transactions,
    "get_terminal_status": get_terminal_status,
}

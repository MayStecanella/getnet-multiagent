from datetime import date, timedelta

from app.tools.account import get_settlements, get_terminal_status, get_transactions


def test_settlements_of_yesterday():
    out = get_settlements("cliente1988")
    assert out["found"] and out["sales_date"] == (date.today() - timedelta(days=1)).isoformat()
    assert out["items"] and all(i["net"] < i["gross"] or i["fee"] == 0 for i in out["items"])
    # crédito parcelado em 3x gera 3 parcelas
    assert sum(1 for i in out["items"] if i["product"] == "credito_parcelado") == 3


def test_declined_transactions_have_reason():
    out = get_transactions("cliente1988", "recusada")
    assert out["found"] and len(out["transactions"]) == 2
    assert all(t["decline_reason"] for t in out["transactions"])


def test_terminal_status():
    out = get_terminal_status("cliente1988")
    assert out["terminals"][0]["status"] == "conexao_instavel"


def test_unknown_user_is_handled():
    for result in (get_settlements("ninguem"), get_transactions("ninguem"), get_terminal_status("ninguem")):
        assert result["found"] is False


def test_tools_never_leak_other_users():
    out = get_transactions("outro_cliente")
    assert out["found"] is False

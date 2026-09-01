import pytest
from mock_kabu import MockKabu
from autotrader.broker.kabu import KabuBroker
from autotrader.broker.base import Order, BrokerError


@pytest.fixture
def kabu():
    m = MockKabu(); url = m.start()
    yield m, KabuBroker({"base_url": url, "api_password": "apipw", "order_password": "ordpw",
                         "exchange": 1, "account_type": 4, "fund_type": "AA", "front_order_type": 13})
    m.stop()


def test_auth_and_queries(kabu):
    m, b = kabu
    b.connect(); assert b.token == "TOKEN123"
    assert b.cash() == 500000.0
    m.positions.append({"Symbol": "7203", "LeavesQty": 100, "HoldQty": 0, "Price": 2800.0, "Side": "2"})
    m.positions.append({"Symbol": "7203", "LeavesQty": 100, "HoldQty": 0, "Price": 2900.0, "Side": "2"})
    p = b.positions(); assert p["7203"]["qty"] == 200 and p["7203"]["avg"] == 2850.0

def test_wrong_password_raises(kabu):
    m, _ = kabu
    bad = KabuBroker({"base_url": b_url(m), "api_password": "x", "order_password": "ordpw"})
    with pytest.raises(BrokerError): bad.connect()

def b_url(m): return f"http://127.0.0.1:{m._server.server_port}/kabusapi"

def test_send_order_body_and_fill(kabu):
    m, b = kabu
    oid = b.send(Order("7203", "buy", 100, est_price=2800))
    body = m.orders[oid]["body"]
    assert body["Symbol"] == "7203" and body["Side"] == "2" and body["Qty"] == 100
    assert body["FrontOrderType"] == 13 and body["CashMargin"] == 1 and body["AccountType"] == 4 and body["FundType"] == "AA"
    assert b.fills([oid]) == []                          # まだ未約定
    m.execute_all({"7203": 2810.0})
    f = b.fills([oid]); assert len(f) == 1 and f[0].price == 2810.0 and f[0].qty == 100 and f[0].side == "buy"
    sid = b.send(Order("7203", "sell", 100, est_price=2810))
    assert m.orders[sid]["body"]["Side"] == "1" and "FundType" not in m.orders[sid]["body"]

def test_reject_and_cancel(kabu):
    m, b = kabu
    m.reject_next = True
    with pytest.raises(BrokerError) as e: b.send(Order("7203", "buy", 100, est_price=1))
    assert "余力不足" in str(e.value)
    oid = b.send(Order("7203", "buy", 100, est_price=1)); b.cancel(oid); assert m.orders[oid]["State"] == 5

def test_reauth_on_expired_token(kabu):
    m, b = kabu
    b.connect(); m.token = "NEWTOKEN"          # サーバ側でトークンが失効した想定
    b.cash()                                    # 401 → 再認証して成功
    assert b.token == "NEWTOKEN"

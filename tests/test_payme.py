import base64

import pytest
from fastapi.testclient import TestClient

from conftest import make_user, make_plan_active


def auth(key="secret-key"):
    return {"Authorization": "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()}


@pytest.fixture()
def client(env, sent):
    from app.main import app
    with TestClient(app) as c:
        yield c


def rpc(client, method, params, headers=None):
    r = client.post("/payme", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
                    headers=headers if headers is not None else auth())
    assert r.status_code == 200
    return r.json()


def new_order():
    from app import licenses
    uid = make_user()
    p = make_plan_active("m1", 100000)
    return licenses.create_order(uid, p["id"])


def test_auth_required(client):
    o = new_order()
    r = rpc(client, "CheckPerformTransaction", {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}}, headers=auth("bad"))
    assert r["error"]["code"] == -32504
    r = rpc(client, "CheckPerformTransaction", {}, headers={})
    assert r["error"]["code"] == -32504


def test_check_perform_errors(client):
    o = new_order()
    ok = rpc(client, "CheckPerformTransaction", {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}})
    assert ok["result"] == {"allow": True}
    assert rpc(client, "CheckPerformTransaction", {"amount": 5, "account": {"order_id": o["id"]}})["error"]["code"] == -31001
    assert rpc(client, "CheckPerformTransaction", {"amount": o["amount_tiyin"], "account": {"order_id": 9999}})["error"]["code"] == -31050
    assert rpc(client, "CheckPerformTransaction", {"amount": o["amount_tiyin"], "account": {}})["error"]["code"] == -31050


def test_full_flow_issues_token_once(client, sent):
    from app import db
    o = new_order()
    a = {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}}
    c = rpc(client, "CreateTransaction", {"id": "tx1", "time": 1, **a})["result"]
    assert c["state"] == 1
    # takroriy yaratish bir xil javob
    assert rpc(client, "CreateTransaction", {"id": "tx1", "time": 1, **a})["result"]["transaction"] == c["transaction"]
    # shu buyurtmaga boshqa tranzaksiya: band
    assert rpc(client, "CreateTransaction", {"id": "tx2", "time": 1, **a})["error"]["code"] == -31099
    p = rpc(client, "PerformTransaction", {"id": "tx1"})["result"]
    assert p["state"] == 2 and p["perform_time"] > 0
    p2 = rpc(client, "PerformTransaction", {"id": "tx1"})["result"]
    assert p2["perform_time"] == p["perform_time"]
    assert db.one("SELECT COUNT(*) n FROM licenses")["n"] == 1          # token faqat bir marta
    lic = db.one("SELECT * FROM licenses")
    assert lic["status"] == "new" and lic["days"] == 30 and lic["source"] == "payme"
    assert db.one("SELECT status FROM orders WHERE id=?", (o["id"],))["status"] == "paid"
    # to'langan buyurtma qayta to'lanmaydi
    assert rpc(client, "CheckPerformTransaction", a)["error"]["code"] == -31051
    chk = rpc(client, "CheckTransaction", {"id": "tx1"})["result"]
    assert chk["state"] == 2 and chk["reason"] is None
    st = rpc(client, "GetStatement", {"from": 0, "to": 9999999999999})["result"]["transactions"]
    assert len(st) == 1 and st[0]["account"]["order_id"] == o["id"]
    import time
    time.sleep(0.2)                                                      # fon vazifa (xabar) tugashi uchun
    assert any(lic["token"] in t for _, t in sent)


def test_cancel_before_perform(client):
    from app import db
    o = new_order()
    a = {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}}
    rpc(client, "CreateTransaction", {"id": "t1", "time": 1, **a})
    r = rpc(client, "CancelTransaction", {"id": "t1", "reason": 3})["result"]
    assert r["state"] == -1
    assert rpc(client, "PerformTransaction", {"id": "t1"})["error"]["code"] == -31008
    assert db.one("SELECT COUNT(*) n FROM licenses")["n"] == 0
    assert rpc(client, "CancelTransaction", {"id": "nope", "reason": 3})["error"]["code"] == -31003


def test_cancel_after_perform_unactivated_revokes(client):
    from app import db
    o = new_order()
    a = {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}}
    rpc(client, "CreateTransaction", {"id": "t1", "time": 1, **a})
    rpc(client, "PerformTransaction", {"id": "t1"})
    r = rpc(client, "CancelTransaction", {"id": "t1", "reason": 5})["result"]
    assert r["state"] == -2
    assert db.one("SELECT status FROM licenses")["status"] == "revoked"


def test_cancel_after_activation_refused(client):
    from app import db, licenses
    o = new_order()
    a = {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}}
    rpc(client, "CreateTransaction", {"id": "t1", "time": 1, **a})
    rpc(client, "PerformTransaction", {"id": "t1"})
    tok = db.one("SELECT token FROM licenses")["token"]
    assert licenses.activate(tok, {"machine_guid": "g1"})["ok"]
    assert rpc(client, "CancelTransaction", {"id": "t1", "reason": 5})["error"]["code"] == -31007
    assert db.one("SELECT status FROM licenses")["status"] == "active"


def test_timeout_cancels(client):
    from app import db
    o = new_order()
    a = {"amount": o["amount_tiyin"], "account": {"order_id": o["id"]}}
    rpc(client, "CreateTransaction", {"id": "t1", "time": 1, **a})
    db.ex("UPDATE payments SET create_time_ms=create_time_ms-?", (13 * 3600 * 1000,))
    assert rpc(client, "PerformTransaction", {"id": "t1"})["error"]["code"] == -31008
    assert db.one("SELECT state, reason FROM payments")["state"] == -1


def test_unknown_method(client):
    assert rpc(client, "Nope", {})["error"]["code"] == -32601

"""To'lanmagan buyurtma: 1 soatdan keyin eslatma, 12 soatdan keyin avtomatik bekor + xabar."""
import asyncio

from conftest import make_user, make_plan_active


def mk_order(age_s):
    from app import licenses, db
    from app.util import now
    uid = make_user()
    p = make_plan_active("m1", 150000)
    o = licenses.create_order(uid, p["id"])
    db.ex("UPDATE orders SET created_at=? WHERE id=?", (now() - age_s, o["id"]))
    return o["id"]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_warn_after_one_hour_once(env, sent):
    from app import jobs, db
    oid = mk_order(30 * 60)
    assert run(jobs.run_order_jobs()) == (0, 0) and not sent          # 30 daqiqa: hali erta
    db.ex("UPDATE orders SET created_at=created_at-1900 WHERE id=?", (oid,))   # ~1 soat 2 daqiqa
    assert run(jobs.run_order_jobs()) == (1, 0)
    assert len(sent) == 1 and "to'lov hali qilinmadi" in sent[0][1].lower() and "11 soat" in sent[0][1]
    assert run(jobs.run_order_jobs()) == (0, 0) and len(sent) == 1    # ikkinchi marta yuborilmaydi
    assert db.one("SELECT status FROM orders")["status"] == "new"


def test_cancel_after_12h_with_message(env, sent):
    from app import jobs, db
    oid = mk_order(13 * 3600)
    assert run(jobs.run_order_jobs())[1] == 1
    o = db.one("SELECT * FROM orders WHERE id=?", (oid,))
    assert o["status"] == "cancelled" and o["cancel_reason"] == "timeout"
    assert any("bekor qilindi" in m and "12 soat" in m for _, m in sent)
    n = len(sent)
    run(jobs.run_order_jobs())
    assert len(sent) == n                                              # qayta xabar yo'q


def test_open_payme_transaction_cancelled_and_cannot_be_paid(env, sent):
    from app import jobs, db, payme
    oid = mk_order(13 * 3600)
    p = db.one("SELECT * FROM orders WHERE id=?", (oid,))
    from app.util import now_ms
    db.ex("INSERT INTO payments(order_id,payme_id,state,amount_tiyin,create_time_ms) VALUES(?,?,1,?,?)",
          (oid, "tx1", p["amount_tiyin"], now_ms()))
    run(jobs.run_order_jobs())
    pay = db.one("SELECT * FROM payments")
    assert pay["state"] == -1 and pay["reason"] == 4              # ochiq tranzaksiya ham bekor
    # muddati o'tgan buyurtmaga Payme CheckPerform: bekor qilingan
    import pytest
    with pytest.raises(Exception):
        payme.check_perform({"amount": p["amount_tiyin"], "account": {"order_id": oid}})


def test_paid_order_not_touched(env, sent):
    from app import jobs, db
    oid = mk_order(13 * 3600)
    db.ex("UPDATE orders SET status='paid' WHERE id=?", (oid,))
    assert run(jobs.run_order_jobs()) == (0, 0)
    assert db.one("SELECT status FROM orders")["status"] == "paid"

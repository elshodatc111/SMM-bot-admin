"""Bot oqimi: soxta Telegram sessiyasi bilan Dispatcher'ga yangilanishlar beriladi."""
import asyncio
import datetime as dt

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update

from conftest import make_plan_active


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.calls), date=dt.datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=method.text)
        return True

    async def stream_content(self, *a, **k):
        yield b""


def upd(n, **kw):
    base = {"update_id": n}
    base.update(kw)
    return Update.model_validate(base)


def msg(n, uid, text=None, contact=None, chat_id=None):
    m = {"message_id": n, "date": 1700000000, "chat": {"id": uid, "type": "private"},
         "from": {"id": uid, "is_bot": False, "first_name": "Ali"}}
    if text is not None:
        m["text"] = text
    if contact is not None:
        m["contact"] = contact
    return upd(n, message=m)


def cb(n, uid, data):
    return upd(n, callback_query={"id": str(n), "chat_instance": "x", "data": data,
                                  "from": {"id": uid, "is_bot": False, "first_name": "Ali"},
                                  "message": {"message_id": 1, "date": 1700000000, "chat": {"id": uid, "type": "private"},
                                              "from": {"id": 1, "is_bot": True, "first_name": "bot"}, "text": "x"}})


def texts(sess):
    return [c.text for c in sess.calls if isinstance(c, SendMessage)]


@pytest.fixture()
def rig(env, monkeypatch):
    from app import notify
    from app.bot.handlers import build_dispatcher
    sess = FakeSession()
    bot = Bot("123456:ABC", session=sess)
    notify.set_bot(bot)
    dp = build_dispatcher()
    n = [100]

    async def feed(u):
        await dp.feed_update(bot, u)

    def run(coro):
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    yield sess, feed, n
    notify.set_bot(None)


def test_registration_buy_and_my_licenses(rig):
    from app import db, licenses
    sess, feed, n = rig
    loop = asyncio.new_event_loop()
    go = loop.run_until_complete
    U = 5555

    go(feed(msg(1, U, "/start")))
    assert "ro'yxatdan o'ting" in texts(sess)[-1]
    # ro'yxatdan o'tmasdan menyuga kira olmaydi
    go(feed(msg(2, U, "🛒 Litsenziya xarid qilish")))
    assert db.one("SELECT registered FROM users WHERE tg_id=?", (U,))["registered"] == 0
    go(feed(msg(3, U, "/start")))
    go(feed(msg(4, U, "Al")))                                  # juda qisqa
    assert "kamida 3" in texts(sess)[-1]
    go(feed(msg(5, U, "Ali Valiyev")))
    go(feed(msg(6, U, "+998901234567")))                       # matn emas, kontakt kerak
    assert "tugmasini" in texts(sess)[-1]
    go(feed(msg(7, U, contact={"phone_number": "998901234567", "first_name": "Ali", "user_id": 777})))  # boshqa odamniki
    assert db.one("SELECT phone FROM users WHERE tg_id=?", (U,))["phone"] == ""
    go(feed(msg(8, U, contact={"phone_number": "998901234567", "first_name": "Ali", "user_id": U})))
    go(feed(cb(9, U, "reg:0")))
    u = db.one("SELECT * FROM users WHERE tg_id=?", (U,))
    assert u["registered"] == 1 and u["full_name"] == "Ali Valiyev" and u["phone"] == "+998901234567" and u["region"] == "Toshkent shahri"

    # tarif sotuvda yo'q
    go(feed(msg(10, U, "🛒 Litsenziya xarid qilish")))
    assert "tarif yo'q" in texts(sess)[-1]
    p = make_plan_active("m1", 120000)
    go(feed(msg(11, U, "🛒 Litsenziya xarid qilish")))
    go(feed(cb(12, U, f"buy:{p['id']}")))                     # PAYME_MERCHANT_ID sinov uchun berilgan
    o = db.one("SELECT * FROM orders")
    assert o and o["amount_tiyin"] == 12000000 and o["status"] == "new"
    pay_msgs = [c for c in sess.calls if isinstance(c, SendMessage) and c.reply_markup and getattr(c.reply_markup, "inline_keyboard", None)
                and c.reply_markup.inline_keyboard[0][0].url]
    assert pay_msgs and "paycom.uz" in pay_msgs[-1].reply_markup.inline_keyboard[0][0].url

    # Litsenziyalarim
    go(feed(msg(13, U, "🔑 Litsenziyalarim")))
    assert "hali litsenziya yo'q" in texts(sess)[-1]
    lid = licenses.issue_manual(u["id"], p["id"])
    go(feed(msg(14, U, "🔑 Litsenziyalarim")))
    assert db.one("SELECT token FROM licenses")["token"] in texts(sess)[-1]
    loop.close()


def test_device_request_via_bot_and_admin_buttons(rig, monkeypatch):
    from app import db, licenses
    from app.config import cfg
    sess, feed, n = rig
    loop = asyncio.new_event_loop()
    go = loop.run_until_complete
    U = 6001
    db.ex("INSERT INTO users(tg_id,full_name,phone,region,registered,created_at) VALUES(?,?,?,?,1,0)", (U, "Vali", "+99890", "Andijon"))
    uid = db.one("SELECT id FROM users WHERE tg_id=?", (U,))["id"]
    p = make_plan_active("y1", 900000)
    lid = licenses.issue_manual(uid, p["id"])
    tok = licenses.get_license(lid)["token"]
    licenses.activate(tok, {"machine_guid": "G"})
    go(feed(msg(1, U, "🔑 Litsenziyalarim")))
    kb = [c for c in sess.calls if isinstance(c, SendMessage) and c.reply_markup and getattr(c.reply_markup, "inline_keyboard", None)]
    assert kb and kb[-1].reply_markup.inline_keyboard[0][0].callback_data == f"dev:{lid}"
    go(feed(cb(2, U, f"dev:{lid}")))
    go(feed(cb(3, U, f"devok:{lid}")))
    req = db.one("SELECT * FROM device_requests")
    assert req and req["status"] == "pending"
    assert any(c.chat_id == 999 for c in sess.calls if isinstance(c, SendMessage))        # adminga bordi (ADMIN_TG_ID=999)
    # oddiy foydalanuvchi admin tugmasini bosolmaydi
    go(feed(cb(4, U, f"adm:ok:{req['id']}")))
    assert db.one("SELECT status FROM device_requests")["status"] == "pending"
    # admin bosadi
    go(feed(cb(5, 999, f"adm:ok:{req['id']}")))
    assert db.one("SELECT status FROM device_requests")["status"] == "approved"
    assert db.one("SELECT fp_json FROM licenses")["fp_json"] == ""
    assert any("ruxsat" in (c.text or "").lower() for c in sess.calls if isinstance(c, SendMessage) and c.chat_id == U)
    loop.close()


def test_blocked_user_stopped(rig):
    from app import db
    sess, feed, n = rig
    loop = asyncio.new_event_loop()
    go = loop.run_until_complete
    go(feed(msg(1, 7001, "/start")))
    db.ex("UPDATE users SET blocked=1 WHERE tg_id=7001")
    go(feed(msg(2, 7001, "/start")))
    assert "bloklangansiz" in texts(sess)[-1]
    loop.close()


def test_reminders(rig):
    from app import db, licenses, jobs
    from app.util import now
    sess, feed, n = rig
    loop = asyncio.new_event_loop()
    go = loop.run_until_complete
    db.ex("INSERT INTO users(tg_id,full_name,phone,registered,created_at) VALUES(8001,'R','+1',1,0)")
    uid = db.one("SELECT id FROM users")["id"]
    p = make_plan_active("m1", 1000)
    lid = licenses.issue_manual(uid, p["id"])
    tok = licenses.get_license(lid)["token"]
    licenses.activate(tok, {"machine_guid": "G"})
    assert go(jobs.run_reminders()) == 0                       # 30 kun: hali erta
    db.ex("UPDATE licenses SET expires_at=?", (now() + 6 * 86400 + 100,))
    assert go(jobs.run_reminders()) == 1                       # 7 kunlik chegara
    assert go(jobs.run_reminders()) == 0                       # takror yuborilmaydi
    db.ex("UPDATE licenses SET expires_at=?", (now() + 1 * 86400 - 100,))
    assert go(jobs.run_reminders()) == 1                       # 1 kunlik
    db.ex("UPDATE licenses SET expires_at=?", (now() - 10,))
    assert go(jobs.run_reminders()) == 1                       # tugadi
    assert go(jobs.run_reminders()) == 0
    loop.close()

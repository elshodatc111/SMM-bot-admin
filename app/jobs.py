"""Fon vazifalari: muddat eslatmalari, eski buyurtmalarni tozalash, ommaviy xabar yuborish."""
import asyncio
import logging

from . import db, licenses, notify, texts
from .db import q, one, ex
from .util import now

log = logging.getLogger("adminbot.jobs")

SEND_PER_SEC = 20


# ------------------------------------------------------------------ eslatmalar
def _reminder_days() -> list[int]:
    out = []
    for p in db.get_setting("reminder_days", "7,3,1").split(","):
        p = p.strip()
        if p.isdigit() and int(p) > 0:
            out.append(int(p))
    return sorted(set(out), reverse=True)


async def run_reminders() -> int:
    """Faol litsenziyalar uchun eslatma yuboradi. -> yuborilganlar soni."""
    sent = 0
    ts = now()
    marks = _reminder_days()
    rows = q("SELECT l.*, u.tg_id AS tg_id FROM licenses l JOIN users u ON u.id=l.user_id "
             "WHERE l.status='active' AND l.expires_at IS NOT NULL AND l.expires_at<=?",
             (ts + (max(marks) if marks else 0) * 86400,))
    for lic in rows:
        left = licenses.days_left(lic, ts)
        if lic["expires_at"] <= ts:
            marker = 0
        else:
            cand = [m for m in marks if left <= m]       # eng kichik mos chegara
            if not cand:
                continue
            marker = min(cand)
        if one("SELECT 1 FROM reminders_sent WHERE license_id=? AND marker=?", (lic["id"], marker)):
            continue
        # tugash xabaridan keyin boshqa eslatmalar yuborilmasin; kattaroq chegaralar ham o'tkazib yuboriladi
        text = texts.expired(lic) if marker == 0 else texts.reminder(lic, left)
        ok = await notify.send(lic["tg_id"], text)
        ex("INSERT OR IGNORE INTO reminders_sent(license_id,marker,sent_at) VALUES(?,?,?)", (lic["id"], marker, ts))
        if marker == 0:
            await notify.to_admin(f"⌛ Litsenziya tugadi: {lic['plan_title']} · {lic['token']}")
        sent += 1 if ok else 0
    return sent


async def run_order_jobs() -> tuple[int, int]:
    """To'lanmagan buyurtmalar: (1) eslatma, (2) muddati o'tganini bekor qilish va xabar. -> (eslatma, bekor)."""
    from . import payme
    from .config import cfg
    from .bot import keyboards as kb
    ts = now()
    timeout = licenses.order_timeout_s()
    warn = licenses.order_warn_s()
    warned = cancelled = 0
    if warn:
        rows = q("SELECT o.*, u.tg_id AS tg_id FROM orders o JOIN users u ON u.id=o.user_id "
                 "WHERE o.status='new' AND o.warned_at IS NULL AND o.created_at<=? AND o.created_at>?",
                 (ts - warn, ts - timeout))
        for o in rows:
            ex("UPDATE orders SET warned_at=? WHERE id=? AND status='new'", (ts, o["id"]))   # bir marta
            left_h = max(1, -(-(o["created_at"] + timeout - ts) // 3600))
            markup = kb.pay_kb(payme.checkout_url(o, cfg.BASE_URL)) if cfg.PAYME_MERCHANT_ID else None
            await notify.send(o["tg_id"], texts.order_unpaid(o, left_h), reply_markup=markup)
            warned += 1
    cancel_expired = licenses.cancel_expired_orders(ts)
    # xabar yuborilmaganlar (jumladan, oldingi urinishda yetib bormaganlar) — xavfsiz qayta urinish
    rows = q("SELECT o.*, u.tg_id AS tg_id FROM orders o JOIN users u ON u.id=o.user_id "
             "WHERE o.status='cancelled' AND o.cancel_reason='timeout' AND o.cancel_notified_at IS NULL")
    for o in rows:
        ex("UPDATE orders SET cancel_notified_at=? WHERE id=?", (ts, o["id"]))
        await notify.send(o["tg_id"], texts.order_cancelled(o, timeout // 3600))
        cancelled += 1
    return warned, cancelled


# ------------------------------------------------------------------ ommaviy xabar
AUDIENCES = {
    "all": "Hamma ro'yxatdan o'tganlar",
    "active": "Faol litsenziyasi borlar",
    "expiring": "Muddati 7 kun ichida tugaydiganlar",
    "expired": "Muddati tugaganlar",
    "never": "Hali xarid qilmaganlar",
}


def audience_users(audience: str):
    base = "SELECT u.* FROM users u WHERE u.registered=1 AND u.blocked=0 AND u.bot_blocked=0 "
    ts = now()
    if audience == "active":
        return q(base + "AND u.id IN (SELECT user_id FROM licenses WHERE status='active' AND expires_at>?)", (ts,))
    if audience == "expiring":
        return q(base + "AND u.id IN (SELECT user_id FROM licenses WHERE status='active' AND expires_at>? AND expires_at<=?)",
                 (ts, ts + 7 * 86400))
    if audience == "expired":
        return q(base + "AND u.id IN (SELECT user_id FROM licenses WHERE status='active' AND expires_at<=?) "
                        "AND u.id NOT IN (SELECT user_id FROM licenses WHERE status='active' AND expires_at>?)", (ts, ts))
    if audience == "never":
        return q(base + "AND u.id NOT IN (SELECT user_id FROM licenses)")
    return q(base)


def create_broadcast(text: str, audience: str) -> int:
    users = audience_users(audience)
    bid = ex("INSERT INTO broadcasts(text,audience,status,created_at,total) VALUES(?,?, 'running',?,?)",
             (text, audience, now(), len(users)))
    with db.tx() as c:
        c.executemany("INSERT INTO broadcast_items(broadcast_id,user_id,tg_id) VALUES(?,?,?)",
                      [(bid, u["id"], u["tg_id"]) for u in users])
    if not users:
        ex("UPDATE broadcasts SET status='done', finished_at=? WHERE id=?", (now(), bid))
    return bid


async def broadcast_tick() -> int:
    """Bitta partiya (~1 soniyalik) yuboradi. -> shu safar ishlangan elementlar soni."""
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
    b = one("SELECT * FROM broadcasts WHERE status='running' ORDER BY id LIMIT 1")
    if not b:
        return 0
    items = q("SELECT * FROM broadcast_items WHERE broadcast_id=? AND status='pending' ORDER BY id LIMIT ?",
              (b["id"], SEND_PER_SEC))
    bot = notify.get_bot()
    if bot is None:
        return 0
    done = 0
    for it in items:
        try:
            await bot.send_message(it["tg_id"], b["text"])
            ex("UPDATE broadcast_items SET status='sent' WHERE id=?", (it["id"],))
            ex("UPDATE broadcasts SET sent=sent+1 WHERE id=?", (b["id"],))
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(int(e.retry_after) + 1, 60))
            break
        except TelegramForbiddenError:
            ex("UPDATE users SET bot_blocked=1 WHERE id=?", (it["user_id"],))
            ex("UPDATE broadcast_items SET status='failed', error='bloklagan' WHERE id=?", (it["id"],))
            ex("UPDATE broadcasts SET failed=failed+1 WHERE id=?", (b["id"],))
        except (TelegramBadRequest, Exception) as e:
            ex("UPDATE broadcast_items SET status='failed', error=? WHERE id=?", (str(e)[:200], it["id"]))
            ex("UPDATE broadcasts SET failed=failed+1 WHERE id=?", (b["id"],))
        done += 1
        await asyncio.sleep(1 / SEND_PER_SEC)
    left = one("SELECT COUNT(*) n FROM broadcast_items WHERE broadcast_id=? AND status='pending'", (b["id"],))["n"]
    if left == 0:
        ex("UPDATE broadcasts SET status='done', finished_at=? WHERE id=? AND status='running'", (now(), b["id"]))
    return done


def cancel_broadcast(bid: int):
    ex("UPDATE broadcasts SET status='cancelled', finished_at=? WHERE id=? AND status='running'", (now(), bid))


# ------------------------------------------------------------------ asosiy sikl
async def main_loop():
    last_slow = 0
    last_orders = 0
    while True:
        try:
            n = await broadcast_tick()
            if now() - last_orders > 60:
                last_orders = now()
                await run_order_jobs()
            if now() - last_slow > 600:
                last_slow = now()
                await run_reminders()
            await asyncio.sleep(1 if n == 0 else 0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job loop error")
            await asyncio.sleep(5)

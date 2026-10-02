"""Kunlik statistika va boshqaruv paneli ko'rsatkichlari (jadvallardan hisoblanadi, alohida saqlanmaydi)."""
from datetime import datetime

from ..config import cfg
from ..db import q, one
from ..util import now, day_start, month_start, tz


def daily(days: int = 30) -> list[dict]:
    """So'nggi `days` kun (yangidan eskiga): yangi foydalanuvchi, to'lov soni, tushum, aktivlashtirish."""
    off = cfg.tz_seconds
    since = day_start() - (days - 1) * 86400
    out = {}
    for i in range(days):
        d = datetime.fromtimestamp(since + i * 86400, tz()).strftime("%Y-%m-%d")
        out[d] = {"date": d, "users": 0, "orders": 0, "paid": 0, "revenue": 0, "issued": 0, "activations": 0}

    def fill(sql, key, field="n"):
        for r in q(sql, (off, since)):
            if r["d"] in out:
                out[r["d"]][key] = r[field] or 0

    fill("SELECT date(registered_at+?, 'unixepoch') d, COUNT(*) n FROM users WHERE registered=1 AND registered_at>=? GROUP BY d", "users")
    fill("SELECT date(created_at+?, 'unixepoch') d, COUNT(*) n FROM orders WHERE created_at>=? GROUP BY d", "orders")
    fill("SELECT date(paid_at+?, 'unixepoch') d, COUNT(*) n FROM orders WHERE status='paid' AND paid_at>=? GROUP BY d", "paid")
    fill("SELECT date(paid_at+?, 'unixepoch') d, SUM(amount_tiyin)/100 n FROM orders WHERE status='paid' AND paid_at>=? GROUP BY d", "revenue")
    fill("SELECT date(created_at+?, 'unixepoch') d, COUNT(*) n FROM licenses WHERE created_at>=? GROUP BY d", "issued")
    fill("SELECT date(activated_at+?, 'unixepoch') d, COUNT(*) n FROM licenses WHERE activated_at>=? GROUP BY d", "activations")
    return sorted(out.values(), key=lambda r: r["date"], reverse=True)


def summary() -> dict:
    ts = now()
    ms = month_start()
    return {
        "users_total": one("SELECT COUNT(*) n FROM users")["n"],
        "users_reg": one("SELECT COUNT(*) n FROM users WHERE registered=1")["n"],
        "users_today": one("SELECT COUNT(*) n FROM users WHERE registered=1 AND registered_at>=?", (day_start(),))["n"],
        "lic_active": one("SELECT COUNT(*) n FROM licenses WHERE status='active' AND expires_at>?", (ts,))["n"],
        "lic_new": one("SELECT COUNT(*) n FROM licenses WHERE status='new'")["n"],
        "lic_expiring": one("SELECT COUNT(*) n FROM licenses WHERE status='active' AND expires_at>? AND expires_at<=?",
                            (ts, ts + 7 * 86400))["n"],
        "rev_total": (one("SELECT SUM(amount_tiyin) s FROM orders WHERE status='paid'")["s"] or 0) // 100,
        "rev_month": (one("SELECT SUM(amount_tiyin) s FROM orders WHERE status='paid' AND paid_at>=?", (ms,))["s"] or 0) // 100,
        "rev_today": (one("SELECT SUM(amount_tiyin) s FROM orders WHERE status='paid' AND paid_at>=?", (day_start(),))["s"] or 0) // 100,
        "pending_dev": one("SELECT COUNT(*) n FROM device_requests WHERE status='pending'")["n"],
        "bot_blocked": one("SELECT COUNT(*) n FROM users WHERE bot_blocked=1")["n"],
    }

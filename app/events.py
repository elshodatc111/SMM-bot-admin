"""Payme `after` amallari: to'lov bajarilgach foydalanuvchi va adminga xabar."""
import logging

from . import licenses, notify, texts
from .db import one
from .util import esc, fmt_uzs

log = logging.getLogger("adminbot.events")


async def run_after(after: list):
    for kind, lid in after:
        try:
            lic = licenses.get_license(lid) if lid else None
            if not lic:
                continue
            u = one("SELECT * FROM users WHERE id=?", (lic["user_id"],))
            if kind == "paid":
                order = one("SELECT * FROM orders WHERE id=?", (lic["order_id"],))
                await notify.send(u["tg_id"], texts.license_issued(lic))
                await notify.to_admin(
                    f"💰 <b>Yangi to'lov</b>\n{esc(u['full_name'])} ({esc(u['phone'])})\n"
                    f"{esc(lic['plan_title'])} — {fmt_uzs(order['amount_tiyin'] // 100)} so'm")
            elif kind == "refunded":
                await notify.send(u["tg_id"], "ℹ️ To'lovingiz Payme tomonidan bekor qilindi, litsenziya o'chirildi.")
                await notify.to_admin(f"↩️ Payme to'lovni bekor qildi: {esc(u['full_name'])}, {esc(lic['plan_title'])}")
        except Exception:
            log.exception("after action failed")

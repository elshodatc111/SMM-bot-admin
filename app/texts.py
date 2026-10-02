"""Bot matnlari (HTML)."""
from . import licenses
from .util import esc, fmt_dt, fmt_uzs

BTN_BUY = "🛒 Litsenziya xarid qilish"
BTN_MY = "🔑 Litsenziyalarim"
BTN_PAY = "💳 To'lovlarim"
BTN_HELP = "ℹ️ Yordam"
BTN_PROFILE = "👤 Profil"

STATUS_LABEL = {
    "new": "⏳ Aktivlashtirilmagan",
    "active": "✅ Faol",
    "expired": "⌛ Muddati tugagan",
    "revoked": "⛔ Bekor qilingan",
}

HOW_TO = ("Tokenni dasturda «Litsenziya» bo'limiga kiriting. Aktivlashtirilgan paytdan boshlab muddat hisoblanadi.")


def license_card(lic, with_token=True) -> str:
    st = licenses.eff_status(lic)
    lines = [f"<b>{esc(lic['plan_title'])}</b> — {STATUS_LABEL[st]}"]
    if with_token:
        lines.append(f"Token: <code>{esc(lic['token'])}</code>")
    if st == "new":
        lines.append("Muddat: umrbod" if licenses.is_lifetime(lic)
                     else f"Muddat: {lic['days']} kun (dasturga kiritilganda boshlanadi)")
    elif st in ("active", "expired"):
        lines.append(f"Aktivlashtirilgan: {fmt_dt(lic['activated_at'])}")
        if licenses.is_lifetime(lic):
            lines.append("Tugash: yo'q (umrbod)")
        else:
            lines.append(f"Tugash: {fmt_dt(lic['expires_at'])}" + (f" (qoldi: {licenses.days_left(lic)} kun)" if st == "active" else ""))
    return "\n".join(lines)


def license_issued(lic) -> str:
    return ("✅ <b>To'lov qabul qilindi!</b>\n\n"
            f"Tarif: <b>{esc(lic['plan_title'])}</b>\n"
            f"Sizning litsenziya tokeningiz:\n<code>{esc(lic['token'])}</code>\n\n"
            f"{HOW_TO}\n\nToken bir martalik: u birinchi kiritilgan kompyuterga bog'lanadi. "
            "Keyinroq «🔑 Litsenziyalarim» bo'limidan ko'rishingiz mumkin.")


def order_text(order) -> str:
    return (f"Tarif: <b>{esc(order['plan_title'])}</b> ({order['days']} kun)\n"
            f"Summa: <b>{fmt_uzs(order['amount_tiyin'] // 100)} so'm</b>\n\n"
            f"To'lash uchun tugmani bosing. To'lov {licenses.order_timeout_s() // 3600} soat ichida qilinmasa, buyurtma avtomatik bekor bo'ladi. "
            "To'lov tasdiqlangach token shu yerga avtomatik keladi.")


def order_unpaid(order, left_hours: int) -> str:
    return (f"⏳ <b>To'lov hali qilinmadi</b>\n\nTarif: <b>{esc(order['plan_title'])}</b>, "
            f"{fmt_uzs(order['amount_tiyin'] // 100)} so'm.\n"
            f"Buyurtma №{order['id']} uchun to'lovni <b>{left_hours} soat ichida</b> amalga oshiring, "
            "aks holda buyurtma avtomatik bekor qilinadi.")


def order_cancelled(order, hours: int) -> str:
    return (f"❌ <b>Buyurtma bekor qilindi</b>\n\nTarif: <b>{esc(order['plan_title'])}</b>, "
            f"{fmt_uzs(order['amount_tiyin'] // 100)} so'm (buyurtma №{order['id']}).\n"
            f"{hours} soat ichida to'lov qilinmagani uchun buyurtma avtomatik bekor qilindi. "
            f"Hisobingizdan pul yechilmagan. Litsenziya olish uchun «{BTN_BUY}» orqali qaytadan buyurtma bering.")


def reminder(lic, left: int) -> str:
    return (f"⏰ <b>Litsenziya muddati tugayapti</b>\n\n{esc(lic['plan_title'])} litsenziyangiz tugashiga "
            f"<b>{left} kun</b> qoldi ({fmt_dt(lic['expires_at'])}).\nUzluksiz ishlash uchun yangisini xarid qiling: «{BTN_BUY}».")


def expired(lic) -> str:
    return (f"⌛ <b>Litsenziya muddati tugadi</b>\n\n{esc(lic['plan_title'])} ({fmt_dt(lic['expires_at'])}).\n"
            f"Davom ettirish uchun «{BTN_BUY}» orqali yangisini oling.")

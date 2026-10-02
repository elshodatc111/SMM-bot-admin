"""Telegram bot: ro'yxatdan o'tish, xarid, litsenziyalar, qurilma almashtirish."""
import logging
import re

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import db, licenses, notify, payme, texts
from ..config import cfg
from ..db import one, q, ex
from ..util import esc, fmt_dt, fmt_uzs, now
from . import keyboards as kb
from .regions import REGIONS

log = logging.getLogger("adminbot.bot")


class Reg(StatesGroup):
    name = State()
    phone = State()
    region = State()


# ------------------------------------------------------------------ foydalanuvchi middleware
class UserMiddleware(BaseMiddleware):
    """Har bir yangilanishda foydalanuvchini bazadan oladi/yaratadi va bloklanganlarni to'xtatadi."""

    async def __call__(self, handler, event, data):
        tg = data.get("event_from_user")
        if tg is None or tg.is_bot:
            return await handler(event, data)
        name = " ".join(x for x in (tg.first_name, tg.last_name) if x)
        row = one("SELECT * FROM users WHERE tg_id=?", (tg.id,))
        if not row:
            ex("INSERT INTO users(tg_id,username,tg_name,created_at,last_seen) VALUES(?,?,?,?,?)",
               (tg.id, tg.username or "", name, now(), now()))
        else:
            ex("UPDATE users SET username=?, tg_name=?, last_seen=?, bot_blocked=0 WHERE tg_id=?",
               (tg.username or "", name, now(), tg.id))
        data["user"] = one("SELECT * FROM users WHERE tg_id=?", (tg.id,))
        if data["user"]["blocked"] and tg.id != cfg.admin_tg_id:
            if isinstance(event, Message):
                await event.answer("Siz bloklangansiz. Admin bilan bog'laning.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Siz bloklangansiz.", show_alert=True)
            return None
        return await handler(event, data)


class Registered(Filter):
    async def __call__(self, event, user=None) -> bool:
        return bool(user and user["registered"])


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and cfg.admin_tg_id and u.id == cfg.admin_tg_id)


reg_router = Router(name="reg")
main_router = Router(name="main")
admin_router = Router(name="adm")
fallback_router = Router(name="fallback")


# ------------------------------------------------------------------ ro'yxatdan o'tish
@reg_router.message(CommandStart())
async def start(m: Message, state: FSMContext, user):
    await state.clear()
    if user["registered"]:
        await m.answer(db.get_setting("welcome_text"), reply_markup=kb.main_menu())
        return
    await m.answer(db.get_setting("welcome_text") + "\n\nBotdan foydalanish uchun avval ro'yxatdan o'ting.\n"
                   "<b>Ism va familiyangizni</b> yozing:", reply_markup=kb.remove())
    await state.set_state(Reg.name)


@reg_router.message(Reg.name, F.text)
async def reg_name(m: Message, state: FSMContext):
    name = re.sub(r"\s+", " ", m.text).strip()
    if len(name) < 3 or len(name) > 80 or name.startswith("/"):
        await m.answer("Iltimos, ism va familiyangizni to'liq yozing (kamida 3 ta belgi).")
        return
    await state.update_data(full_name=name)
    await state.set_state(Reg.phone)
    await m.answer("Endi telefon raqamingizni pastdagi tugma orqali yuboring:", reply_markup=kb.phone_kb())


@reg_router.message(Reg.phone, F.contact)
async def reg_phone(m: Message, state: FSMContext):
    c = m.contact
    if c.user_id != m.from_user.id:
        await m.answer("Iltimos, o'zingizning raqamingizni tugma orqali yuboring.", reply_markup=kb.phone_kb())
        return
    phone = c.phone_number if c.phone_number.startswith("+") else "+" + c.phone_number
    await state.update_data(phone=phone)
    await state.set_state(Reg.region)
    await m.answer("Qaysi viloyat/shaharda yashaysiz?", reply_markup=kb.remove())
    await m.answer("Tanlang:", reply_markup=kb.regions_kb())


@reg_router.message(Reg.phone)
async def reg_phone_bad(m: Message):
    await m.answer("Raqamni yozib yubormang, «📱 Raqamni yuborish» tugmasini bosing.", reply_markup=kb.phone_kb())


@reg_router.callback_query(Reg.region, F.data.startswith("reg:"))
async def reg_region(cb: CallbackQuery, state: FSMContext, user):
    try:
        region = REGIONS[int(cb.data.split(":")[1])]
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    if not data.get("full_name") or not data.get("phone"):
        await state.clear()
        await cb.message.answer("Ma'lumotlar yo'qoldi, /start ni bosib qaytadan boshlang.")
        await cb.answer()
        return
    ex("UPDATE users SET full_name=?, phone=?, region=?, registered=1, registered_at=? WHERE id=?",
       (data["full_name"], data["phone"], region, now(), user["id"]))
    await state.clear()
    await cb.message.edit_text(f"Rahmat, <b>{esc(data['full_name'])}</b>! Ro'yxatdan o'tdingiz ✅")
    await cb.message.answer("Asosiy menyu:", reply_markup=kb.main_menu())
    await cb.answer()
    await notify.to_admin(f"👤 <b>Yangi foydalanuvchi</b>\n{esc(data['full_name'])}\n{esc(data['phone'])} · {esc(region)}")


# ------------------------------------------------------------------ asosiy menyu (faqat ro'yxatdan o'tganlar)
main_router.message.filter(Registered())
main_router.callback_query.filter(Registered())


@main_router.message(F.text == texts.BTN_BUY)
async def buy_menu(m: Message):
    ps = licenses.plans(active_only=True)
    if not ps:
        await m.answer("Hozircha sotuvda tarif yo'q. Keyinroq urinib ko'ring.")
        return
    await m.answer("Tarifni tanlang:", reply_markup=kb.plans_kb(ps))


@main_router.callback_query(F.data.startswith("buy:"))
async def buy_plan(cb: CallbackQuery, user):
    if not cfg.PAYME_MERCHANT_ID:
        await cb.answer("To'lov tizimi hali sozlanmagan.", show_alert=True)
        return
    order = licenses.create_order(user["id"], int(cb.data.split(":")[1]))
    if not order:
        await cb.answer("Bu tarif hozir sotuvda emas.", show_alert=True)
        return
    url = payme.checkout_url(order, cfg.BASE_URL)
    await cb.message.answer(texts.order_text(order), reply_markup=kb.pay_kb(url))
    await cb.answer()


@main_router.message(F.text == texts.BTN_MY)
async def my_licenses(m: Message, user):
    rows = q("SELECT * FROM licenses WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],))
    if not rows:
        await m.answer("Sizda hali litsenziya yo'q. «" + texts.BTN_BUY + "» orqali xarid qiling.")
        return
    for lic in rows:
        ok, _ = licenses.device_request_state(lic)
        await m.answer(texts.license_card(lic), reply_markup=kb.device_kb(lic["id"]) if ok else None)


@main_router.message(F.text == texts.BTN_PAY)
async def my_payments(m: Message, user):
    rows = q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user["id"],))
    if not rows:
        await m.answer("To'lovlar yo'q.")
        return
    lab = {"new": "⏳ kutilmoqda", "paid": "✅ to'langan", "cancelled": "❌ bekor"}
    await m.answer("\n".join(
        f"{fmt_dt(o['created_at'])} · {esc(o['plan_title'])} · {fmt_uzs(o['amount_tiyin'] // 100)} so'm · {lab[o['status']]}"
        for o in rows))


@main_router.message(F.text == texts.BTN_PROFILE)
async def profile(m: Message, user):
    await m.answer(f"👤 <b>{esc(user['full_name'])}</b>\n📱 {esc(user['phone'])}\n📍 {esc(user['region'])}")


@main_router.message(F.text == texts.BTN_HELP)
async def help_(m: Message):
    await m.answer(esc(db.get_setting("support_text")) + "\n\n" + texts.HOW_TO)


@main_router.message(Command("menu"))
async def menu(m: Message):
    await m.answer("Asosiy menyu:", reply_markup=kb.main_menu())


# ------------------------------------------------------------------ qurilmani almashtirish
@main_router.callback_query(F.data.startswith("dev:"))
async def dev_ask(cb: CallbackQuery, user):
    lic = licenses.get_license(int(cb.data.split(":")[1]))
    if not lic or lic["user_id"] != user["id"]:
        await cb.answer("Topilmadi", show_alert=True)
        return
    ok, why = licenses.device_request_state(lic)
    if not ok:
        await cb.answer(why, show_alert=True)
        return
    left = licenses.resets_limit() - lic["device_resets_used"]
    await cb.message.answer(
        "Litsenziyani boshqa kompyuterga ko'chirish uchun admin ruxsati kerak. Ruxsat berilgach hozirgi kompyuterga "
        f"bog'lanish o'chadi va tokenni yangi kompyuterda kiritasiz.\nQolgan imkoniyat: <b>{left}</b>.\nDavom etamizmi?",
        reply_markup=kb.device_confirm_kb(lic["id"]))
    await cb.answer()


@main_router.callback_query(F.data == "devno")
async def dev_no(cb: CallbackQuery):
    await cb.message.edit_text("Bekor qilindi.")
    await cb.answer()


@main_router.callback_query(F.data.startswith("devok:"))
async def dev_ok(cb: CallbackQuery, user):
    ok, why, rid = licenses.request_device_change(user["id"], int(cb.data.split(":")[1]))
    if not ok:
        await cb.answer(why, show_alert=True)
        return
    await cb.message.edit_text("📨 So'rovingiz adminga yuborildi. Javob shu yerga keladi.")
    lic = licenses.get_license(int(cb.data.split(":")[1]))
    await notify.to_admin(
        f"🔄 <b>Qurilma almashtirish so'rovi</b>\n{esc(user['full_name'])} · {esc(user['phone'])}\n"
        f"{esc(lic['plan_title'])} · <code>{esc(lic['token'])}</code>\n"
        f"Ishlatilgan almashtirish: {lic['device_resets_used']}/{licenses.resets_limit()}",
        reply_markup=kb.admin_device_kb(rid))
    await cb.answer()


# ------------------------------------------------------------------ admin: tugma orqali ruxsat
admin_router.callback_query.filter(IsAdmin())


@admin_router.callback_query(F.data.startswith("adm:"))
async def adm_resolve(cb: CallbackQuery):
    _, act, rid = cb.data.split(":")
    r = licenses.resolve_device_request(int(rid), act == "ok", "")
    if not r:
        await cb.answer("Allaqachon hal qilingan", show_alert=True)
        return
    u = one("SELECT * FROM users WHERE id=?", (r["user_id"],))
    if act == "ok":
        await notify.send(u["tg_id"], "✅ Qurilmani almashtirishga ruxsat berildi. Endi tokeningizni yangi "
                                      "kompyuterdagi dasturga kiriting (muddat davom etadi).")
        await cb.message.edit_text(cb.message.html_text + "\n\n✅ <b>Ruxsat berildi</b>")
    else:
        await notify.send(u["tg_id"], "❌ Qurilmani almashtirish so'rovi rad etildi. Savol bo'lsa admin bilan bog'laning.")
        await cb.message.edit_text(cb.message.html_text + "\n\n❌ <b>Rad etildi</b>")
    await cb.answer()


# ------------------------------------------------------------------ boshqa hamma narsa
@fallback_router.message()
async def fallback(m: Message, user, state: FSMContext):
    if not user["registered"]:
        await m.answer("Avval ro'yxatdan o'ting: /start ni bosing.")
    else:
        await m.answer("Menyudan tanlang:", reply_markup=kb.main_menu())


def build_dispatcher():
    from aiogram import Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    dp = Dispatcher(storage=MemoryStorage())
    mw = UserMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
    routers = (reg_router, admin_router, main_router, fallback_router)
    for r in routers:                      # sinovda bir necha marta qurish mumkin bo'lsin
        r._parent_router = None
    dp.include_routers(*routers)
    return dp

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .. import texts
from .regions import REGIONS


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text=texts.BTN_BUY)],
        [KeyboardButton(text=texts.BTN_MY), KeyboardButton(text=texts.BTN_PAY)],
        [KeyboardButton(text=texts.BTN_PROFILE), KeyboardButton(text=texts.BTN_HELP)],
    ])


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
        [KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]])


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def regions_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, r in enumerate(REGIONS):
        row.append(InlineKeyboardButton(text=r, callback_data=f"reg:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_kb(plans) -> InlineKeyboardMarkup:
    from ..util import fmt_uzs
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{p['title']} — {fmt_uzs(p['price_uzs'])} so'm", callback_data=f"buy:{p['id']}")]
        for p in plans])


def pay_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Payme orqali to'lash", url=url)]])


def device_kb(lid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Qurilmani almashtirish", callback_data=f"dev:{lid}")]])


def device_confirm_kb(lid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, so'rov yuborish", callback_data=f"devok:{lid}"),
        InlineKeyboardButton(text="Bekor qilish", callback_data="devno")]])


def admin_device_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ruxsat berish", callback_data=f"adm:ok:{req_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm:no:{req_id}")]])

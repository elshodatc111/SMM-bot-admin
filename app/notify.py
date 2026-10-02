"""Telegramga xabar yuborish yordamchisi. Sinovda `send` ni almashtirish mumkin."""
import logging

log = logging.getLogger("adminbot.notify")

_bot = None


def set_bot(bot):
    global _bot
    _bot = bot


def get_bot():
    return _bot


async def send(tg_id: int, text: str, reply_markup=None) -> bool:
    """True = yuborildi. Foydalanuvchi botni bloklagan bo'lsa bazaga belgilab qo'yadi."""
    if _bot is None or not tg_id:
        return False
    from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
    try:
        await _bot.send_message(tg_id, text, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        from . import db
        db.ex("UPDATE users SET bot_blocked=1 WHERE tg_id=?", (tg_id,))
        return False
    except TelegramBadRequest as e:
        log.warning("send %s: %s", tg_id, e)
        return False
    except Exception as e:  # tarmoq xatosi
        log.warning("send %s failed: %s", tg_id, e)
        return False


async def to_admin(text: str, reply_markup=None) -> bool:
    from .config import cfg
    return await send(cfg.admin_tg_id, text, reply_markup)

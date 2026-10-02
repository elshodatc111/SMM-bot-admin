"""Webhookni qo'lda o'rnatish/tekshirish: python tools/set_webhook.py [info|set|delete]"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiogram import Bot  # noqa: E402
from app.config import cfg  # noqa: E402


async def main(cmd):
    bot = Bot(cfg.BOT_TOKEN)
    try:
        if cmd == "set":
            await bot.set_webhook(f"{cfg.BASE_URL}/tg/webhook/{cfg.WEBHOOK_SECRET}", secret_token=cfg.WEBHOOK_SECRET[:64])
        elif cmd == "delete":
            await bot.delete_webhook()
        info = await bot.get_webhook_info()
        print("url:", (info.url or "").replace(cfg.WEBHOOK_SECRET, "***"), "| kutilayotgan:", info.pending_update_count,
              "| oxirgi xato:", info.last_error_message)
    finally:
        await bot.session.close()


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "info"))

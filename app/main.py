"""FastAPI ilova: Telegram webhook, Payme, litsenziya API, admin panel."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import api, db, events, jobs, notify, payme
from .config import cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("adminbot")

_dp = None
_tasks: set = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def _webhook_ready() -> tuple[bool, str]:
    """Webhook uchun https:// manzil va maxfiy so'z kerak (Telegram http:// va localhost'ni qabul qilmaydi)."""
    import re
    if not cfg.BASE_URL.startswith("https://"):
        return False, "BASE_URL https:// bilan boshlanmaydi yoki bo'sh"
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", cfg.WEBHOOK_SECRET or ""):
        return False, "WEBHOOK_SECRET yo'q yoki juda qisqa/noto'g'ri belgilar (tools/gen_secrets.py)"
    return True, ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dp
    db.conn().close()                      # sxema va boshlang'ich ma'lumotlar
    bg = []
    mode = cfg.BOT_MODE.lower()
    bot = None
    if cfg.BOT_TOKEN and mode != "off":
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from .bot.handlers import build_dispatcher
        bot = Bot(cfg.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            if mode != "polling":
                ok, why = _webhook_ready()
                if not ok:
                    log.warning("Webhook rejimi ishlamaydi (%s). Polling rejimiga o'tildi: kompyuterda sinash uchun "
                                "shu yetarli. Serverda BASE_URL va WEBHOOK_SECRET ni to'ldiring.", why)
                    mode = "polling"
            notify.set_bot(bot)
            _dp = build_dispatcher()
            if mode == "polling":
                await bot.delete_webhook()
                bg.append(asyncio.create_task(_dp.start_polling(bot, handle_signals=False)))
                log.info("Bot polling rejimida ishlayapti")
            else:
                await bot.set_webhook(f"{cfg.BASE_URL}/tg/webhook/{cfg.WEBHOOK_SECRET}",
                                      secret_token=cfg.WEBHOOK_SECRET[:64],
                                      allowed_updates=_dp.resolve_used_update_types())
                log.info("Webhook o'rnatildi: %s/tg/webhook/***", cfg.BASE_URL)
            bg.append(asyncio.create_task(jobs.main_loop()))
        except Exception as e:             # noto'g'ri token/tarmoq: admin panel baribir ishlasin
            log.error("Bot ishga tushmadi: %s. Admin panel bot'siz ishlaydi; .env ni tekshiring.", e)
            notify.set_bot(None)
            _dp = None
            await bot.session.close()
            bot = None
    elif not cfg.BOT_TOKEN:
        log.warning("BOT_TOKEN berilmagan: faqat admin panel ishlayapti.")
    try:
        yield
    finally:
        for t in bg:
            t.cancel()
        if bot is not None:
            await bot.session.close()


app = FastAPI(title="AdminBot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api.router)

from .admin import router as admin_router, install as install_admin  # noqa: E402
app.include_router(admin_router)
install_admin(app)


@app.get("/")
async def root():
    return PlainTextResponse("OK")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/payme")
async def payme_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    res, after = payme.handle(request.headers.get("authorization"), body)
    if after:
        _spawn(events.run_after(after))
    return JSONResponse(res)


@app.post("/tg/webhook/{secret}")
async def tg_webhook(secret: str, request: Request):
    if not cfg.WEBHOOK_SECRET or secret != cfg.WEBHOOK_SECRET or _dp is None:
        return PlainTextResponse("forbidden", status_code=403)
    hdr = request.headers.get("x-telegram-bot-api-secret-token")
    if hdr != cfg.WEBHOOK_SECRET[:64]:
        return PlainTextResponse("forbidden", status_code=403)
    from aiogram.types import Update
    bot = notify.get_bot()
    update = Update.model_validate(await request.json(), context={"bot": bot})
    _spawn(_dp.feed_update(bot, update))
    return {"ok": True}

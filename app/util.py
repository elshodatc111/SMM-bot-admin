import re
import secrets
import time
from datetime import datetime, timezone, timedelta

from .config import cfg

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 0/O, 1/I chalkashmasligi uchun


def now() -> int:
    return int(time.time())


def now_ms() -> int:
    return int(time.time() * 1000)


def new_token() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    return "TGP-" + "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize_token(s: str) -> str:
    """Foydalanuvchi kiritgan tokenni standart ko'rinishga keltiradi (bo'sh joy, kichik harf, chiziqchasiz)."""
    s = re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()
    if s.startswith("TGP"):
        s = s[3:]
    if len(s) != 16:
        return ""
    return "TGP-" + "-".join(s[i:i + 4] for i in range(0, 16, 4))


def tz() -> timezone:
    return timezone(timedelta(seconds=cfg.tz_seconds))


def fmt_dt(ts, with_time=True) -> str:
    if not ts:
        return "—"
    d = datetime.fromtimestamp(int(ts), tz())
    return d.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def fmt_uzs(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def day_start(ts: int | None = None) -> int:
    """Toshkent bo'yicha bugungi kun boshlanishi (UTC epoch)."""
    ts = ts if ts is not None else now()
    off = cfg.tz_seconds
    return ((ts + off) // 86400) * 86400 - off


def month_start(ts: int | None = None) -> int:
    ts = ts if ts is not None else now()
    d = datetime.fromtimestamp(ts, tz())
    return int(d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


def esc(s) -> str:
    """Telegram HTML uchun."""
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

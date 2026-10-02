"""Sozlamalar: .env fayli yoki muhit o'zgaruvchilari. Qiymatlar har safar o'qiladi (sinov uchun qulay)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file():
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_env_file()

_DEFAULTS = {
    "BOT_TOKEN": "",
    "BOT_MODE": "webhook",            # webhook | polling | off
    "BASE_URL": "",
    "WEBHOOK_SECRET": "",
    "ADMIN_PASSWORD_HASH": "",
    "SESSION_SECRET": "",
    "ADMIN_TG_ID": "",
    "PAYME_MERCHANT_ID": "",
    "PAYME_KEY": "",
    "PAYME_LOGIN": "Paycom",
    "PAYME_TEST": "1",
    "DB_PATH": str(ROOT / "data" / "adminbot.sqlite3"),
    "ED25519_PRIVATE_KEY": "",        # bo'sh bo'lsa data/ed25519.key avtomatik yaratiladi
    "TZ_OFFSET_HOURS": "5",           # Asia/Tashkent
}


class _Cfg:
    def __getattr__(self, name):
        if name in _DEFAULTS:
            return os.environ.get(name, _DEFAULTS[name])
        raise AttributeError(name)

    def flag(self, name: str) -> bool:
        return str(getattr(self, name)).strip().lower() in ("1", "true", "yes", "on")

    @property
    def admin_tg_id(self) -> int:
        try:
            return int(self.ADMIN_TG_ID)
        except ValueError:
            return 0

    @property
    def tz_seconds(self) -> int:
        return int(float(self.TZ_OFFSET_HOURS) * 3600)


cfg = _Cfg()

"""Bitta admin uchun kirish: parol xeshi (PBKDF2), imzolangan cookie, CSRF, kirishga urinishlarni cheklash."""
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from ..config import cfg

COOKIE = "adm"
SESSION_TTL = 12 * 3600
_fails: dict[str, list] = {}       # ip -> [urinishlar, bloklangan_vaqtgacha]


def hash_password(password: str, iterations: int = 200_000) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2${iterations}${salt}${h}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def _secret() -> bytes:
    s = cfg.SESSION_SECRET or cfg.ADMIN_PASSWORD_HASH
    return s.encode() or b"insecure-dev-secret"


def _sig(msg: str) -> str:
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    exp = int(time.time()) + SESSION_TTL
    nonce = secrets.token_hex(8)
    body = f"{exp}.{nonce}"
    return f"{body}.{_sig(body)}"


def session_valid(value: str | None) -> bool:
    if not value:
        return False
    try:
        exp, nonce, sig = value.split(".")
        return hmac.compare_digest(sig, _sig(f"{exp}.{nonce}")) and int(exp) > time.time()
    except Exception:
        return False


def csrf_for(value: str | None) -> str:
    return _sig("csrf:" + (value or ""))[:32]


def is_admin(request: Request) -> bool:
    return session_valid(request.cookies.get(COOKIE))


def csrf_token(request: Request) -> str:
    return csrf_for(request.cookies.get(COOKIE))


class NeedLogin(Exception):
    pass


def require_admin(request: Request):
    if not is_admin(request):
        raise NeedLogin()


async def require_csrf(request: Request):
    """POST so'rovlarda forma maydoni `csrf` tekshiriladi."""
    require_admin(request)
    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf", "")), csrf_token(request)):
        raise HTTPException(403, "CSRF tekshiruvi o'tmadi")


def locked(ip: str) -> bool:
    f = _fails.get(ip)
    return bool(f and f[1] > time.time())


def register_fail(ip: str):
    f = _fails.setdefault(ip, [0, 0])
    f[0] += 1
    if f[0] >= 5:
        f[1] = time.time() + 600
        f[0] = 0


def register_ok(ip: str):
    _fails.pop(ip, None)

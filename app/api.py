"""Dastur uchun litsenziya API (JSON). Hamma javob 200, natija `ok`/`code` maydonida."""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import licenses

router = APIRouter(prefix="/api/v1/license")

_hits: dict[str, deque] = defaultdict(deque)
LIMIT, WINDOW = 30, 60      # bir IP dan daqiqasiga 30 ta so'rov


def _limited(ip: str) -> bool:
    t = time.time()
    d = _hits[ip]
    while d and t - d[0] > WINDOW:
        d.popleft()
    if len(d) >= LIMIT:
        return True
    d.append(t)
    if len(_hits) > 5000:
        for k in [k for k, v in _hits.items() if not v][:1000]:
            _hits.pop(k, None)
    return False


async def _run(request: Request, create: bool):
    ip = request.client.host if request.client else ""
    if _limited(ip):
        return JSONResponse({"ok": False, "code": "rate_limited", "message": "Juda ko'p so'rov. Biroz kuting."}, 429)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(licenses._response("bad_request"))
    if not isinstance(body, dict):
        return JSONResponse(licenses._response("bad_request"))
    res = licenses.activate(str(body.get("token", "")), body.get("fp"), ip=ip,
                            app_version=str(body.get("app_version", ""))[:30], create=create)
    return JSONResponse(res)


@router.post("/activate")
async def activate(request: Request):
    return await _run(request, True)


@router.post("/check")
async def check(request: Request):
    return await _run(request, False)

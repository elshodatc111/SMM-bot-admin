"""Veb admin panel (faqat bitta admin)."""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import db, jobs, licenses, notify
from ..config import cfg
from ..db import one, q, ex
from ..signing import public_key_b64
from ..util import esc, fmt_dt, fmt_uzs, now
from . import auth, exports, stats
from .auth import NeedLogin, require_admin, require_csrf

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["dt"] = fmt_dt
templates.env.filters["uzs"] = fmt_uzs
templates.env.globals["eff_status"] = licenses.eff_status
templates.env.globals["days_left"] = licenses.days_left
templates.env.globals["is_yearly"] = licenses.is_yearly
templates.env.globals["audiences"] = jobs.AUDIENCES

STATUS_UZ = {"new": "aktivlashtirilmagan", "active": "faol", "expired": "muddati tugagan", "revoked": "bekor qilingan"}
ORDER_UZ = {"new": "kutilmoqda", "paid": "to'langan", "cancelled": "bekor"}
templates.env.globals["STATUS_UZ"] = STATUS_UZ
templates.env.globals["ORDER_UZ"] = ORDER_UZ
templates.env.globals["LIC_KIND"] = {"active": "ok", "new": "info", "expired": "warn", "revoked": "bad"}
templates.env.globals["ORD_KIND"] = {"new": "warn", "paid": "ok", "cancelled": "bad"}

router = APIRouter(prefix="/admin")
PAGE = 50


def install(app):
    @app.exception_handler(NeedLogin)
    async def _need_login(request, exc):
        return RedirectResponse("/admin/login", status_code=303)


def render(request: Request, name: str, **ctx):
    ctx.update(request=request, csrf=auth.csrf_token(request), msg=request.query_params.get("msg", ""),
               pending_dev=one("SELECT COUNT(*) n FROM device_requests WHERE status='pending'")["n"],
               err=request.query_params.get("err", ""), nav=ctx.get("nav", ""))
    return templates.TemplateResponse(request, name, ctx)


def back(url: str, msg: str = "", err: str = ""):
    sep = "&" if "?" in url else "?"
    if msg:
        url += f"{sep}msg={quote(msg)}"
    elif err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ kirish
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request, "err": ""})


@router.post("/login")
async def login(request: Request, password: str = Form("")):
    ip = request.client.host if request.client else ""
    if auth.locked(ip):
        return templates.TemplateResponse(request, "login.html",
                                          {"request": request, "err": "Juda ko'p urinish. 10 daqiqadan keyin urinib ko'ring."}, status_code=429)
    if not cfg.ADMIN_PASSWORD_HASH:
        return templates.TemplateResponse(request, "login.html",
                                          {"request": request, "err": "ADMIN_PASSWORD_HASH sozlanmagan (.env)."})
    if not cfg.ADMIN_PASSWORD_HASH.startswith("pbkdf2$"):
        return templates.TemplateResponse(request, "login.html", {"request": request, "err":
            "ADMIN_PASSWORD_HASH xesh emas (parolning o'zi yozilgan). tools\\make_admin_hash.py ni ishga tushirib, "
            "u chiqargan 'pbkdf2$...' qatorini .env ga qo'ying."})
    if not auth.verify_password(password, cfg.ADMIN_PASSWORD_HASH):
        auth.register_fail(ip)
        return templates.TemplateResponse(request, "login.html", {"request": request, "err": "Parol noto'g'ri."}, status_code=401)
    auth.register_ok(ip)
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.SESSION_TTL, httponly=True, samesite="strict",
                    secure=cfg.BASE_URL.startswith("https"), path="/")
    return resp


@router.post("/logout")
async def logout(_=Depends(require_csrf)):
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# ------------------------------------------------------------------ bosh sahifa / statistika
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_admin)):
    return render(request, "dashboard.html", nav="dash", s=stats.summary(), days=stats.daily(14),
                  recent=q("SELECT o.*, u.full_name, u.phone FROM orders o JOIN users u ON u.id=o.user_id "
                           "WHERE o.status='paid' ORDER BY o.paid_at DESC LIMIT 8"))


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, days: int = 30, _=Depends(require_admin)):
    days = min(max(days, 7), 365)
    rows = stats.daily(days)
    tot = {k: sum(r[k] for r in rows) for k in ("users", "orders", "paid", "revenue", "issued", "activations")}
    return render(request, "stats.html", nav="stats", rows=rows, tot=tot, ndays=days)


# ------------------------------------------------------------------ foydalanuvchilar
@router.get("/users", response_class=HTMLResponse)
async def users(request: Request, qs: str = "", page: int = 1, _=Depends(require_admin)):
    where, args = "WHERE 1=1", []
    if qs.strip():
        like = f"%{qs.strip()}%"
        where += " AND (full_name LIKE ? OR phone LIKE ? OR username LIKE ? OR CAST(tg_id AS TEXT) LIKE ? OR tg_name LIKE ?)"
        args += [like] * 5
    total = one(f"SELECT COUNT(*) n FROM users {where}", args)["n"]
    rows = q(f"SELECT u.*, (SELECT COUNT(*) FROM licenses l WHERE l.user_id=u.id) lic_n FROM users u {where} "
             "ORDER BY u.id DESC LIMIT ? OFFSET ?", args + [PAGE, (max(page, 1) - 1) * PAGE])
    return render(request, "users.html", nav="users", rows=rows, qs=qs, page=page, total=total, per=PAGE)


@router.get("/users/{uid}", response_class=HTMLResponse)
async def user_detail(request: Request, uid: int, _=Depends(require_admin)):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return back("/admin/users", err="Topilmadi")
    return render(request, "user.html", nav="users", u=u,
                  lics=q("SELECT * FROM licenses WHERE user_id=? ORDER BY id DESC", (uid,)),
                  orders=q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (uid,)),
                  plans=licenses.plans())


@router.post("/users/{uid}/block")
async def user_block(uid: int, _=Depends(require_csrf)):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    if u:
        ex("UPDATE users SET blocked=? WHERE id=?", (0 if u["blocked"] else 1, uid))
        db.audit("admin", "user_block", f"{uid}={0 if u['blocked'] else 1}")
    return back(f"/admin/users/{uid}", "Saqlandi")


@router.post("/users/{uid}/message")
async def user_message(uid: int, text: str = Form(""), _=Depends(require_csrf)):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    if not u or not text.strip():
        return back(f"/admin/users/{uid}", err="Matn bo'sh")
    ok = await notify.send(u["tg_id"], text)
    return back(f"/admin/users/{uid}", "Yuborildi" if ok else "", "" if ok else "Yuborib bo'lmadi (bot bloklangan bo'lishi mumkin)")


@router.post("/users/{uid}/grant")
async def user_grant(uid: int, plan_id: str = Form(...), note: str = Form(""), _=Depends(require_csrf)):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    if plan_id == "lifetime":
        if not u:
            return back("/admin/users", err="Topilmadi")
        lid = licenses.issue_lifetime(uid, note)
    else:
        if not u or not plan_id.isdigit() or not licenses.get_plan(int(plan_id)):
            return back("/admin/users", err="Topilmadi")
        lid = licenses.issue_manual(uid, int(plan_id), note)
    lic = licenses.get_license(lid)
    from .. import texts
    await notify.send(u["tg_id"], "🎁 Sizga litsenziya berildi.\n\n" + texts.license_issued(lic).split("\n\n", 1)[1])
    db.audit("admin", "grant", f"user={uid} lic={lid}")
    return back(f"/admin/licenses/{lid}", "Litsenziya berildi va foydalanuvchiga yuborildi")


# ------------------------------------------------------------------ tariflar
@router.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, _=Depends(require_admin)):
    return render(request, "plans.html", nav="plans", plans=licenses.plans())


@router.post("/plans/{pid}")
async def plan_save(pid: int, title: str = Form(...), price: str = Form("0"), days: int = Form(...),
                    active: str = Form(""), _=Depends(require_csrf)):
    try:
        p = int(price.replace(" ", "").replace(",", ""))
    except ValueError:
        return back("/admin/plans", err="Narx noto'g'ri")
    if p < 0 or days < 1 or not title.strip():
        return back("/admin/plans", err="Qiymatlar noto'g'ri")
    act = 1 if (active and p > 0) else 0
    ex("UPDATE plans SET title=?, price_uzs=?, days=?, active=? WHERE id=?", (title.strip(), p, days, act, pid))
    db.audit("admin", "plan_save", f"{pid}: {title} {p} {days} {act}")
    return back("/admin/plans", "Saqlandi" + ("" if act or not active else " (narx 0 bo'lgani uchun tarif o'chiq qoldi)"))


# ------------------------------------------------------------------ to'lovlar
@router.get("/payments", response_class=HTMLResponse)
async def payments(request: Request, status: str = "", page: int = 1, _=Depends(require_admin)):
    where, args = "WHERE 1=1", []
    if status in ("new", "paid", "cancelled"):
        where += " AND o.status=?"
        args.append(status)
    total = one(f"SELECT COUNT(*) n FROM orders o {where}", args)["n"]
    rows = q(f"SELECT o.*, u.full_name, u.phone, "
             "(SELECT payme_id FROM payments p WHERE p.order_id=o.id ORDER BY p.id DESC LIMIT 1) pid, "
             "(SELECT state FROM payments p WHERE p.order_id=o.id ORDER BY p.id DESC LIMIT 1) pstate "
             f"FROM orders o JOIN users u ON u.id=o.user_id {where} ORDER BY o.id DESC LIMIT ? OFFSET ?",
             args + [PAGE, (max(page, 1) - 1) * PAGE])
    return render(request, "payments.html", nav="pay", rows=rows, status=status, page=page, total=total, per=PAGE)


# ------------------------------------------------------------------ litsenziyalar
@router.get("/licenses", response_class=HTMLResponse)
async def licenses_page(request: Request, status: str = "", qs: str = "", page: int = 1, _=Depends(require_admin)):
    ts = now()
    where, args = "WHERE 1=1", []
    if status == "new":
        where += " AND l.status='new'"
    elif status == "active":
        where += " AND l.status='active' AND l.expires_at>?"
        args.append(ts)
    elif status == "expired":
        where += " AND l.status='active' AND l.expires_at<=?"
        args.append(ts)
    elif status == "revoked":
        where += " AND l.status='revoked'"
    if qs.strip():
        like = f"%{qs.strip()}%"
        where += " AND (l.token LIKE ? OR u.full_name LIKE ? OR u.phone LIKE ?)"
        args += [like] * 3
    total = one(f"SELECT COUNT(*) n FROM licenses l JOIN users u ON u.id=l.user_id {where}", args)["n"]
    rows = q(f"SELECT l.*, u.full_name, u.phone FROM licenses l JOIN users u ON u.id=l.user_id {where} "
             "ORDER BY l.id DESC LIMIT ? OFFSET ?", args + [PAGE, (max(page, 1) - 1) * PAGE])
    return render(request, "licenses.html", nav="lic", rows=rows, status=status, qs=qs, page=page, total=total, per=PAGE)


@router.get("/licenses/{lid}", response_class=HTMLResponse)
async def license_detail(request: Request, lid: int, _=Depends(require_admin)):
    lic = licenses.get_license(lid)
    if not lic:
        return back("/admin/licenses", err="Topilmadi")
    return render(request, "license.html", nav="lic", lic=lic, u=one("SELECT * FROM users WHERE id=?", (lic["user_id"],)),
                  order=one("SELECT * FROM orders WHERE id=?", (lic["order_id"],)) if lic["order_id"] else None,
                  reqs=q("SELECT * FROM device_requests WHERE license_id=? ORDER BY id DESC", (lid,)),
                  limit=licenses.resets_limit())


async def _lic_notify(lic, text):
    u = one("SELECT * FROM users WHERE id=?", (lic["user_id"],))
    if u:
        await notify.send(u["tg_id"], text)


@router.post("/licenses/{lid}/revoke")
async def lic_revoke(lid: int, note: str = Form(""), _=Depends(require_csrf)):
    lic = licenses.get_license(lid)
    if lic:
        licenses.revoke(lid, note)
        db.audit("admin", "revoke", str(lid))
        await _lic_notify(lic, f"⛔ <b>{esc(lic['plan_title'])}</b> litsenziyangiz bekor qilindi. Savol bo'lsa admin bilan bog'laning.")
    return back(f"/admin/licenses/{lid}", "Litsenziya muddatdan oldin tugatildi")


@router.post("/licenses/{lid}/unrevoke")
async def lic_unrevoke(lid: int, _=Depends(require_csrf)):
    licenses.unrevoke(lid)
    db.audit("admin", "unrevoke", str(lid))
    return back(f"/admin/licenses/{lid}", "Qayta tiklandi")


@router.post("/licenses/{lid}/extend")
async def lic_extend(lid: int, days: int = Form(0), _=Depends(require_csrf)):
    licenses.extend(lid, max(-3650, min(days, 3650)))
    db.audit("admin", "extend", f"{lid} {days}")
    return back(f"/admin/licenses/{lid}", f"Muddat {days:+d} kunga o'zgartirildi")


@router.post("/licenses/{lid}/reset")
async def lic_reset(lid: int, count: str = Form(""), _=Depends(require_csrf)):
    lic = licenses.get_license(lid)
    if lic:
        licenses.admin_reset_device(lid, bool(count))
        db.audit("admin", "reset_device", f"{lid} count={bool(count)}")
        await _lic_notify(lic, "✅ Litsenziyangiz kompyuterga bog'lanishi bo'shatildi. Tokenni yangi kompyuterda kiritishingiz mumkin.")
    return back(f"/admin/licenses/{lid}", "Qurilma bo'shatildi")


@router.post("/licenses/{lid}/note")
async def lic_note(lid: int, note: str = Form(""), _=Depends(require_csrf)):
    ex("UPDATE licenses SET note=? WHERE id=?", (note[:500], lid))
    return back(f"/admin/licenses/{lid}", "Saqlandi")


# ------------------------------------------------------------------ qurilma so'rovlari
@router.get("/devices", response_class=HTMLResponse)
async def devices(request: Request, _=Depends(require_admin)):
    rows = q("SELECT r.*, u.full_name, u.phone, l.token, l.plan_title, l.device_resets_used, l.activated_at "
             "FROM device_requests r JOIN users u ON u.id=r.user_id JOIN licenses l ON l.id=r.license_id "
             "ORDER BY (r.status='pending') DESC, r.id DESC LIMIT 200")
    return render(request, "devices.html", nav="dev", rows=rows, limit=licenses.resets_limit())


async def _resolve(rid: int, approve: bool):
    r = licenses.resolve_device_request(rid, approve)
    if r:
        u = one("SELECT * FROM users WHERE id=?", (r["user_id"],))
        if approve:
            await notify.send(u["tg_id"], "✅ Qurilmani almashtirishga ruxsat berildi. Tokeningizni yangi kompyuterdagi dasturga kiriting.")
        else:
            await notify.send(u["tg_id"], "❌ Qurilmani almashtirish so'rovi rad etildi. Savol bo'lsa admin bilan bog'laning.")
        db.audit("admin", "device_" + ("ok" if approve else "no"), str(rid))
    return r


@router.post("/devices/{rid}/approve")
async def dev_approve(rid: int, _=Depends(require_csrf)):
    r = await _resolve(rid, True)
    return back("/admin/devices", "Ruxsat berildi" if r else "", "" if r else "Allaqachon hal qilingan")


@router.post("/devices/{rid}/reject")
async def dev_reject(rid: int, _=Depends(require_csrf)):
    r = await _resolve(rid, False)
    return back("/admin/devices", "Rad etildi" if r else "", "" if r else "Allaqachon hal qilingan")


# ------------------------------------------------------------------ ommaviy xabar
@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, _=Depends(require_admin)):
    counts = {k: len(jobs.audience_users(k)) for k in jobs.AUDIENCES}
    return render(request, "broadcast.html", nav="bc", items=q("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 30"),
                  counts=counts, running=bool(one("SELECT 1 FROM broadcasts WHERE status='running'")))


@router.post("/broadcast")
async def broadcast_send(text: str = Form(""), audience: str = Form("all"), _=Depends(require_csrf)):
    if not text.strip() or audience not in jobs.AUDIENCES:
        return back("/admin/broadcast", err="Matn yoki auditoriya noto'g'ri")
    if not notify.get_bot():
        return back("/admin/broadcast", err="Bot ishlamayapti (BOT_TOKEN/BOT_MODE)")
    bid = jobs.create_broadcast(text.strip(), audience)
    db.audit("admin", "broadcast", str(bid))
    return back("/admin/broadcast", f"Yuborish boshlandi (#{bid})")


@router.post("/broadcast/test")
async def broadcast_test(text: str = Form(""), _=Depends(require_csrf)):
    if not text.strip():
        return back("/admin/broadcast", err="Matn bo'sh")
    ok = await notify.to_admin(text.strip())
    return back("/admin/broadcast", "Test sizga yuborildi" if ok else "", "" if ok else "Yuborib bo'lmadi (ADMIN_TG_ID/bot)")


@router.post("/broadcast/{bid}/cancel")
async def broadcast_cancel(bid: int, _=Depends(require_csrf)):
    jobs.cancel_broadcast(bid)
    return back("/admin/broadcast", "To'xtatildi")


# ------------------------------------------------------------------ sozlamalar
SETTING_KEYS = ("welcome_text", "support_text", "reminder_days", "device_resets_per_license",
                "order_timeout_hours", "order_warn_minutes")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _=Depends(require_admin)):
    vals = {k: db.get_setting(k) for k in SETTING_KEYS}
    return render(request, "settings.html", nav="set", vals=vals, pubkey=public_key_b64(),
                  payme_url=f"{cfg.BASE_URL}/payme", payme_test=cfg.flag("PAYME_TEST"),
                  info={"bot": bool(notify.get_bot()), "mode": cfg.BOT_MODE, "merchant": bool(cfg.PAYME_MERCHANT_ID),
                        "key": bool(cfg.PAYME_KEY), "admin_id": bool(cfg.admin_tg_id)})


@router.post("/settings")
async def settings_save(welcome_text: str = Form(""), support_text: str = Form(""), reminder_days: str = Form("7,3,1"),
                        device_resets_per_license: int = Form(2), order_timeout_hours: int = Form(12),
                        order_warn_minutes: int = Form(60), _=Depends(require_csrf)):
    days = [p.strip() for p in reminder_days.split(",") if p.strip().isdigit() and int(p.strip()) > 0]
    db.set_setting("welcome_text", welcome_text.strip())
    db.set_setting("support_text", support_text.strip())
    db.set_setting("reminder_days", ",".join(days) or "7,3,1")
    db.set_setting("device_resets_per_license", str(max(0, min(device_resets_per_license, 20))))
    hours = max(1, min(order_timeout_hours, 48))
    db.set_setting("order_timeout_hours", str(hours))
    db.set_setting("order_warn_minutes", str(max(0, min(order_warn_minutes, hours * 60 - 1))))
    return back("/admin/settings", "Saqlandi")


# ------------------------------------------------------------------ Excel
@router.get("/export/{kind}.xlsx")
async def export(kind: str, days: int = 90, _=Depends(require_admin)):
    fn = exports.KINDS.get(kind)
    if not fn:
        return back("/admin/", err="Noma'lum eksport")
    data = fn(days) if kind == "stats" else fn()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{kind}_{fmt_dt(now(), False)}.xlsx"'})

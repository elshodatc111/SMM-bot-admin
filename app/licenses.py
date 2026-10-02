"""Tarif, buyurtma, litsenziya va qurilma mantiqi (Telegram/Payme/veb bilan bog'liq emas)."""
import hashlib
import json
import sqlite3

from . import db
from .db import q, one, ex, tx
from .signing import sign
from .util import now, new_token

YEARLY_MIN_DAYS = 365
LIFETIME_DAYS = 0            # days=0 — umrbod litsenziya (faqat adminning qo'lda berishi)
LIFETIME_EXP = 4102444800    # 2100-01-01: umrbod litsenziya shu sanagacha "tugamaydi"
FP_KEYS = ("machine_guid", "board", "disk", "cpu", "mac")


# ---------------------------------------------------------------- tariflar / buyurtma
def plans(active_only=False):
    sql = "SELECT * FROM plans" + (" WHERE active=1 AND price_uzs>0" if active_only else "") + " ORDER BY sort,id"
    return q(sql)


def get_plan(plan_id: int):
    return one("SELECT * FROM plans WHERE id=?", (plan_id,))


def create_order(user_id: int, plan_id: int):
    p = get_plan(plan_id)
    if not p or not p["active"] or p["price_uzs"] <= 0:
        return None
    oid = ex("INSERT INTO orders(user_id,plan_id,plan_title,days,amount_tiyin,status,created_at) VALUES(?,?,?,?,?,'new',?)",
             (user_id, p["id"], p["title"], p["days"], int(p["price_uzs"]) * 100, now()))
    return one("SELECT * FROM orders WHERE id=?", (oid,))


def order_timeout_s() -> int:
    v = db.get_setting("order_timeout_hours", "12")
    h = int(v) if str(v).isdigit() and 1 <= int(v) <= 48 else 12
    return h * 3600


def order_warn_s() -> int:
    """0 = o'chirilgan. Muddatdan kichik bo'lishi shart."""
    v = db.get_setting("order_warn_minutes", "60")
    m = int(v) if str(v).isdigit() else 60
    s = m * 60
    return s if 0 < s < order_timeout_s() else 0


def cancel_expired_orders(ts: int | None = None) -> list[int]:
    """Muddati o'tgan (to'lanmagan) buyurtmalarni bekor qiladi; ochiq Payme tranzaksiyasi ham bekor (reason=4).
    Poyga bo'lmasligi uchun bitta tranzaksiyada. -> bekor qilingan buyurtma id lari."""
    ts = ts or now()
    done = []
    for o in q("SELECT id FROM orders WHERE status='new' AND created_at<=?", (ts - order_timeout_s(),)):
        with tx() as c:
            r = c.execute("UPDATE orders SET status='cancelled', cancel_reason='timeout' WHERE id=? AND status='new'",
                          (o["id"],))
            if r.rowcount:
                c.execute("UPDATE payments SET state=-1, reason=4, cancel_time_ms=? WHERE order_id=? AND state=1",
                          (ts * 1000, o["id"]))
                done.append(o["id"])
    return done


# ---------------------------------------------------------------- litsenziya yaratish
def _insert_license(c: sqlite3.Connection, user_id, plan_id, title, days, order_id, source, note="") -> int:
    for _ in range(10):
        token = new_token()
        try:
            cur = c.execute(
                "INSERT INTO licenses(user_id,order_id,plan_id,plan_title,days,token,status,source,created_at,note) "
                "VALUES(?,?,?,?,?,?,'new',?,?,?)",
                (user_id, order_id, plan_id, title, days, token, source, now(), note))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Token yaratib bo'lmadi")


def issue_for_order(c: sqlite3.Connection, order) -> int:
    """To'lov bajarilganda (tx ichida) chaqiriladi."""
    lid = _insert_license(c, order["user_id"], order["plan_id"], order["plan_title"], order["days"],
                          order["id"], "payme")
    c.execute("UPDATE orders SET status='paid', paid_at=?, license_id=? WHERE id=?", (now(), lid, order["id"]))
    return lid


def issue_manual(user_id: int, plan_id: int, note="") -> int:
    p = get_plan(plan_id)
    with tx() as c:
        return _insert_license(c, user_id, p["id"], p["title"], p["days"], None, "admin", note)


def issue_lifetime(user_id: int, note="") -> int:
    """Umrbod litsenziya: sotuvda yo'q, faqat admin paneldan beriladi."""
    with tx() as c:
        return _insert_license(c, user_id, None, "Umrbod", LIFETIME_DAYS, None, "admin", note)


def is_lifetime(lic) -> bool:
    return int(lic["days"]) == LIFETIME_DAYS


def get_license(lid: int):
    return one("SELECT * FROM licenses WHERE id=?", (lid,))


# ---------------------------------------------------------------- holat
def eff_status(lic, ts: int | None = None) -> str:
    ts = ts or now()
    if lic["status"] == "revoked":
        return "revoked"
    if lic["status"] == "active":
        return "expired" if (lic["expires_at"] or 0) <= ts else "active"
    return "new"


def days_left(lic, ts: int | None = None) -> int:
    ts = ts or now()
    if lic["status"] != "active" or not lic["expires_at"]:
        return 0
    rest = lic["expires_at"] - ts
    return max(0, -(-rest // 86400))   # yuqoriga yaxlitlash


def is_yearly(lic) -> bool:
    # umrbod ham yillik qoidasiga kiradi (qurilma almashtirish — baribir admin ruxsati bilan)
    return is_lifetime(lic) or int(lic["days"]) >= YEARLY_MIN_DAYS


# ---------------------------------------------------------------- barmoq izi
def _norm_fp(fp) -> dict:
    out = {}
    if isinstance(fp, dict):
        for k in FP_KEYS:
            v = fp.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip().lower()[:200]
    return out


def fp_hash(comp: dict) -> str:
    return hashlib.sha256(json.dumps(comp, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fp_match(stored: dict, new: dict) -> bool:
    """Windows yangilanishi/qayta o'rnatishdan keyin ham o'sha kompyuterni tanish:
    machine_guid mos kelsa yoki boshqa komponentlardan kamida 2 tasi mos kelsa."""
    if not stored or not new:
        return False
    same = [k for k in FP_KEYS if k in stored and k in new and stored[k] == new[k]]
    if "machine_guid" in same:
        return True
    return len([k for k in same if k != "machine_guid"]) >= 2


# ---------------------------------------------------------------- aktivlashtirish (dastur chaqiradi)
MESSAGES = {
    "ok": "Litsenziya faol.",
    "invalid_token": "Token noto'g'ri. Botdagi tokenni aynan shu ko'rinishda kiriting.",
    "revoked": "Bu litsenziya bekor qilingan. Admin bilan bog'laning.",
    "expired": "Litsenziya muddati tugagan. Botdan yangisini xarid qiling.",
    "device_mismatch": "Bu token boshqa kompyuterga bog'langan. Kompyuterni almashtirish uchun botdagi "
                       "«Qurilmani almashtirish» tugmasi orqali admindan ruxsat so'rang (faqat yillik tarif).",
    "not_activated": "Token hali aktivlashtirilmagan.",
    "bad_request": "So'rov noto'g'ri.",
}


def _response(code: str, lic=None, comp=None) -> dict:
    res = {"ok": code == "ok", "code": code, "message": MESSAGES.get(code, code)}
    if code in ("ok", "expired") and lic is not None:
        payload = {
            "token_hash": hashlib.sha256(lic["token"].encode()).hexdigest()[:16],
            "plan": lic["plan_title"],
            "days": int(lic["days"]),
            "lifetime": is_lifetime(lic),
            "activated_at": lic["activated_at"],
            "expires_at": lic["expires_at"],
            "server_time": now(),
            "fp_hash": lic["fp_hash"],
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        res["payload"] = text
        res["sig"] = sign(text)
    return res


def activate(token: str, fp, ip: str = "", app_version: str = "", create: bool = True) -> dict:
    """create=True: yangi token birinchi marta aktivlashtiriladi; False: faqat tekshiruv."""
    from .util import normalize_token
    token = normalize_token(token)
    comp = _norm_fp(fp)
    if not token or not comp:
        return _response("bad_request" if not comp else "invalid_token")
    with tx() as c:
        lic = c.execute("SELECT * FROM licenses WHERE token=?", (token,)).fetchone()
        if not lic:
            return _response("invalid_token")
        if lic["status"] == "revoked":
            return _response("revoked")
        ts = now()
        if lic["status"] == "new":
            if not create:
                return _response("not_activated")
            exp = LIFETIME_EXP if is_lifetime(lic) else ts + int(lic["days"]) * 86400
            c.execute("UPDATE licenses SET status='active', activated_at=?, expires_at=?, fp_hash=?, fp_json=?, "
                      "last_check_at=?, last_ip=?, app_version=? WHERE id=?",
                      (ts, exp, fp_hash(comp), json.dumps(comp), ts, ip, app_version, lic["id"]))
            lic = c.execute("SELECT * FROM licenses WHERE id=?", (lic["id"],)).fetchone()
            return _response("ok", lic)
        # faol (yoki muddati o'tgan)
        stored = json.loads(lic["fp_json"] or "{}")
        if stored and not fp_match(stored, comp):
            return _response("device_mismatch")
        if not stored:
            # admin qurilmani bo'shatgan: shu kompyuterga qayta bog'lanadi (muddat davom etadi)
            c.execute("UPDATE licenses SET fp_hash=?, fp_json=? WHERE id=?", (fp_hash(comp), json.dumps(comp), lic["id"]))
        elif stored != comp:
            c.execute("UPDATE licenses SET fp_hash=?, fp_json=? WHERE id=?", (fp_hash(comp), json.dumps(comp), lic["id"]))
        c.execute("UPDATE licenses SET last_check_at=?, last_ip=?, app_version=? WHERE id=?",
                  (ts, ip, app_version, lic["id"]))
        lic = c.execute("SELECT * FROM licenses WHERE id=?", (lic["id"],)).fetchone()
        if (lic["expires_at"] or 0) <= ts:
            return _response("expired", lic)
        return _response("ok", lic)


# ---------------------------------------------------------------- qurilmani almashtirish
def resets_limit() -> int:
    try:
        return int(db.get_setting("device_resets_per_license", "2"))
    except ValueError:
        return 2


def device_request_state(lic) -> tuple[bool, str]:
    """(mumkinmi, sabab_matni). Faqat yillik tarif, faol va bog'langan, limit tugamagan, kutilayotgan so'rov yo'q."""
    if not is_yearly(lic):
        return False, "Qurilmani almashtirish faqat yillik tarif uchun."
    st = eff_status(lic)
    if st != "active":
        return False, "Litsenziya faol emas."
    if not lic["fp_json"]:
        return False, "Litsenziya hali hech qaysi kompyuterga bog'lanmagan: yangi kompyuterda tokenni kiritavering."
    if lic["device_resets_used"] >= resets_limit():
        return False, f"Almashtirish limiti tugagan ({resets_limit()} marta)."
    if one("SELECT 1 FROM device_requests WHERE license_id=? AND status='pending'", (lic["id"],)):
        return False, "So'rovingiz admin ko'rib chiqishi kutilmoqda."
    return True, ""


def request_device_change(user_id: int, lid: int):
    """-> (ok, matn, request_id)"""
    with tx() as c:
        lic = c.execute("SELECT * FROM licenses WHERE id=? AND user_id=?", (lid, user_id)).fetchone()
        if not lic:
            return False, "Litsenziya topilmadi.", None
        pend = c.execute("SELECT 1 FROM device_requests WHERE license_id=? AND status='pending'", (lid,)).fetchone()
        if pend:
            return False, "So'rovingiz admin ko'rib chiqishi kutilmoqda.", None
        ok, why = device_request_state(lic)
        if not ok:
            return False, why, None
        cur = c.execute("INSERT INTO device_requests(license_id,user_id,status,created_at) VALUES(?,?, 'pending',?)",
                        (lid, user_id, now()))
        return True, "", cur.lastrowid


def resolve_device_request(req_id: int, approve: bool, note: str = ""):
    """Admin ruxsat beradi/rad etadi. -> so'rov qatori (yangilangan) yoki None (allaqachon hal qilingan)."""
    with tx() as c:
        r = c.execute("SELECT * FROM device_requests WHERE id=?", (req_id,)).fetchone()
        if not r or r["status"] != "pending":
            return None
        c.execute("UPDATE device_requests SET status=?, resolved_at=?, admin_note=? WHERE id=?",
                  ("approved" if approve else "rejected", now(), note, req_id))
        if approve:
            c.execute("UPDATE licenses SET fp_hash='', fp_json='', device_resets_used=device_resets_used+1 WHERE id=?",
                      (r["license_id"],))
        return c.execute("SELECT * FROM device_requests WHERE id=?", (req_id,)).fetchone()


def admin_reset_device(lid: int, count: bool = False):
    ex("UPDATE licenses SET fp_hash='', fp_json='', device_resets_used=device_resets_used+? WHERE id=?",
       (1 if count else 0, lid))


# ---------------------------------------------------------------- admin amallari
def revoke(lid: int, note: str = ""):
    ex("UPDATE licenses SET status='revoked', note=CASE WHEN ?!='' THEN ? ELSE note END WHERE id=?", (note, note, lid))


def unrevoke(lid: int):
    lic = get_license(lid)
    if not lic or lic["status"] != "revoked":
        return
    st = "active" if lic["activated_at"] else "new"
    ex("UPDATE licenses SET status=? WHERE id=?", (st, lid))


def extend(lid: int, days: int):
    lic = get_license(lid)
    if not lic or days == 0 or is_lifetime(lic):
        return
    if lic["status"] == "active" and lic["expires_at"]:
        ex("UPDATE licenses SET expires_at=expires_at+? WHERE id=?", (days * 86400, lid))
    else:
        ex("UPDATE licenses SET days=MAX(1,days+?) WHERE id=?", (days, lid))

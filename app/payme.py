"""Payme Merchant API (JSON-RPC 2.0, POST /payme). Hujjat: developer.help.paycom.uz

Holatlar: 1 = yaratilgan, 2 = bajarilgan, -1 = bajarilishidan oldin bekor, -2 = bajarilgandan keyin bekor.
Bekor qilish siyosati: litsenziya hali aktivlashtirilmagan bo'lsa to'lov qaytariladi (litsenziya bekor bo'ladi);
aktivlashtirilgan bo'lsa -31007 (qaytarib bo'lmaydi) — pulni qo'lda qaytarish kelishiladi.
"""
import base64
import hmac
import logging

from . import licenses
from .config import cfg
from .db import one, q, tx
from .util import now, now_ms

log = logging.getLogger("adminbot.payme")

TIMEOUT_MS = 12 * 3600 * 1000


class PaymeError(Exception):
    def __init__(self, code, ru, uz, en, data=None):
        self.code, self.msg, self.data = code, {"ru": ru, "uz": uz, "en": en}, data


E_AUTH = lambda: PaymeError(-32504, "Недостаточно привилегий", "Ruxsat yo'q", "Insufficient privilege")
E_METHOD = lambda: PaymeError(-32601, "Метод не найден", "Metod topilmadi", "Method not found")
E_PARAMS = lambda: PaymeError(-32600, "Неверный запрос", "Noto'g'ri so'rov", "Invalid request")
E_AMOUNT = lambda: PaymeError(-31001, "Неверная сумма", "Summa noto'g'ri", "Wrong amount")
E_NOTX = lambda: PaymeError(-31003, "Транзакция не найдена", "Tranzaksiya topilmadi", "Transaction not found")
E_CANT = lambda: PaymeError(-31008, "Невозможно выполнить операцию", "Amalni bajarib bo'lmaydi", "Unable to perform operation")
E_CANT_CANCEL = lambda: PaymeError(-31007, "Заказ выполнен. Невозможно отменить транзакцию",
                                   "Buyurtma bajarilgan, bekor qilib bo'lmaydi", "Order completed, cannot cancel")
E_ORDER_NF = lambda: PaymeError(-31050, "Заказ не найден", "Buyurtma topilmadi", "Order not found", "order_id")
E_ORDER_PAID = lambda: PaymeError(-31051, "Заказ уже оплачен", "Buyurtma allaqachon to'langan", "Order already paid", "order_id")
E_ORDER_CANC = lambda: PaymeError(-31052, "Заказ отменён", "Buyurtma bekor qilingan", "Order cancelled", "order_id")
E_ORDER_BUSY = lambda: PaymeError(-31099, "Заказ занят другой транзакцией", "Buyurtma boshqa tranzaksiya bilan band",
                                  "Order is busy with another transaction", "order_id")


# ------------------------------------------------------------------ yordamchilar
def check_auth(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:
        return False
    expected = f"{cfg.PAYME_LOGIN}:{cfg.PAYME_KEY}"
    return bool(cfg.PAYME_KEY) and hmac.compare_digest(raw.encode(), expected.encode())


def checkout_url(order, return_url: str = "") -> str:
    base = "https://test.paycom.uz" if cfg.flag("PAYME_TEST") else "https://checkout.paycom.uz"
    parts = [f"m={cfg.PAYME_MERCHANT_ID}", f"ac.order_id={order['id']}", f"a={order['amount_tiyin']}", "l=uz"]
    if return_url:
        parts.append(f"c={return_url}")
    return base + "/" + base64.b64encode(";".join(parts).encode()).decode()


def _order_for(c, params, forbid_busy=False):
    acc = params.get("account") or {}
    try:
        oid = int(acc.get("order_id"))
    except (TypeError, ValueError):
        raise E_ORDER_NF()
    o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        raise E_ORDER_NF()
    try:
        amount = int(params.get("amount"))
    except (TypeError, ValueError):
        raise E_AMOUNT()
    if o["status"] == "paid":
        raise E_ORDER_PAID()
    if o["status"] == "cancelled":
        raise E_ORDER_CANC()
    if o["created_at"] <= now() - licenses.order_timeout_s():
        raise E_ORDER_CANC()               # to'lov muddati o'tgan: fon vazifa hali bekor qilmagan bo'lsa ham
    if amount != o["amount_tiyin"]:
        raise E_AMOUNT()
    return o


def _timeout_cancel(c, p):
    c.execute("UPDATE payments SET state=-1, reason=4, cancel_time_ms=? WHERE id=?", (now_ms(), p["id"]))
    c.execute("UPDATE orders SET status='cancelled' WHERE id=? AND status='new'", (p["order_id"],))


def _tx_view(p):
    return {
        "create_time": p["create_time_ms"], "perform_time": p["perform_time_ms"] or 0,
        "cancel_time": p["cancel_time_ms"] or 0, "transaction": str(p["id"]),
        "state": p["state"], "reason": p["reason"],
    }


# ------------------------------------------------------------------ metodlar
def check_perform(params):
    with tx() as c:
        _order_for(c, params)
    return {"allow": True}


def create_transaction(params, after):
    pid = params.get("id")
    if not pid or not isinstance(pid, str):
        raise E_PARAMS()
    err = None
    res = None
    with tx() as c:
        p = c.execute("SELECT * FROM payments WHERE payme_id=?", (pid,)).fetchone()
        if p:
            if p["state"] != 1:
                err = E_CANT()
            elif now_ms() - p["create_time_ms"] > TIMEOUT_MS:
                _timeout_cancel(c, p)          # xato qaytarishdan oldin yozuv saqlansin (rollback bo'lmasin)
                err = E_CANT()
            else:
                res = {"create_time": p["create_time_ms"], "transaction": str(p["id"]), "state": 1}
        else:
            o = _order_for(c, params)
            busy = c.execute("SELECT * FROM payments WHERE order_id=? AND state=1", (o["id"],)).fetchone()
            if busy and now_ms() - busy["create_time_ms"] <= TIMEOUT_MS:
                err = E_ORDER_BUSY()
            else:
                if busy:
                    _timeout_cancel(c, busy)
                    c.execute("UPDATE orders SET status='new' WHERE id=?", (o["id"],))
                t = now_ms()
                cur = c.execute("INSERT INTO payments(order_id,payme_id,state,amount_tiyin,create_time_ms) VALUES(?,?,1,?,?)",
                                (o["id"], pid, o["amount_tiyin"], t))
                res = {"create_time": t, "transaction": str(cur.lastrowid), "state": 1}
    if err:
        raise err
    return res


def perform_transaction(params, after):
    err = None
    res = None
    lid = None
    with tx() as c:
        p = c.execute("SELECT * FROM payments WHERE payme_id=?", (params.get("id"),)).fetchone()
        if not p:
            raise E_NOTX()
        if p["state"] == 2:
            res = {"transaction": str(p["id"]), "perform_time": p["perform_time_ms"], "state": 2}
        elif p["state"] != 1:
            err = E_CANT()
        elif now_ms() - p["create_time_ms"] > TIMEOUT_MS:
            _timeout_cancel(c, p)
            err = E_CANT()
        else:
            t = now_ms()
            c.execute("UPDATE payments SET state=2, perform_time_ms=? WHERE id=?", (t, p["id"]))
            o = c.execute("SELECT * FROM orders WHERE id=?", (p["order_id"],)).fetchone()
            lid = licenses.issue_for_order(c, o)
            res = {"transaction": str(p["id"]), "perform_time": t, "state": 2}
    if err:
        raise err
    if lid:
        after.append(("paid", lid))
    return res


def cancel_transaction(params, after):
    reason = params.get("reason")
    with tx() as c:
        p = c.execute("SELECT * FROM payments WHERE payme_id=?", (params.get("id"),)).fetchone()
        if not p:
            raise E_NOTX()
        t = now_ms()
        if p["state"] == 1:
            c.execute("UPDATE payments SET state=-1, reason=?, cancel_time_ms=? WHERE id=?", (reason, t, p["id"]))
            c.execute("UPDATE orders SET status='cancelled', cancel_reason='payme' WHERE id=?", (p["order_id"],))
            state, ct = -1, t
        elif p["state"] == 2:
            o = c.execute("SELECT * FROM orders WHERE id=?", (p["order_id"],)).fetchone()
            lic = c.execute("SELECT * FROM licenses WHERE id=?", (o["license_id"],)).fetchone() if o["license_id"] else None
            if lic and lic["status"] == "active":
                raise E_CANT_CANCEL()
            if lic:
                c.execute("UPDATE licenses SET status='revoked', note='To''lov bekor qilindi (Payme)' WHERE id=?", (lic["id"],))
            c.execute("UPDATE payments SET state=-2, reason=?, cancel_time_ms=? WHERE id=?", (reason, t, p["id"]))
            c.execute("UPDATE orders SET status='cancelled' WHERE id=?", (o["id"],))
            state, ct = -2, t
            after.append(("refunded", lic["id"] if lic else 0))
        else:
            state, ct = p["state"], p["cancel_time_ms"]
        return {"transaction": str(p["id"]), "cancel_time": ct, "state": state}


def check_transaction(params):
    p = one("SELECT * FROM payments WHERE payme_id=?", (params.get("id"),))
    if not p:
        raise E_NOTX()
    return _tx_view(p)


def get_statement(params):
    try:
        f, t = int(params.get("from")), int(params.get("to"))
    except (TypeError, ValueError):
        raise E_PARAMS()
    rows = q("SELECT * FROM payments WHERE create_time_ms>=? AND create_time_ms<=? ORDER BY create_time_ms", (f, t))
    out = []
    for p in rows:
        v = _tx_view(p)
        out.append({"id": p["payme_id"], "time": p["create_time_ms"], "amount": p["amount_tiyin"],
                    "account": {"order_id": p["order_id"]}, **v})
    return {"transactions": out}


def handle(auth_header, body) -> tuple[dict, list]:
    """-> (javob dict, keyingi amallar ro'yxati). Telegram xabarlari `after` bo'yicha yuboriladi."""
    after: list = []
    rid = body.get("id") if isinstance(body, dict) else None
    try:
        if not check_auth(auth_header):
            raise E_AUTH()
        if not isinstance(body, dict) or "method" not in body:
            raise E_PARAMS()
        params = body.get("params") or {}
        m = body["method"]
        if m == "CheckPerformTransaction":
            res = check_perform(params)
        elif m == "CreateTransaction":
            res = create_transaction(params, after)
        elif m == "PerformTransaction":
            res = perform_transaction(params, after)
        elif m == "CancelTransaction":
            res = cancel_transaction(params, after)
        elif m == "CheckTransaction":
            res = check_transaction(params)
        elif m == "GetStatement":
            res = get_statement(params)
        else:
            raise E_METHOD()
        return {"jsonrpc": "2.0", "id": rid, "result": res}, after
    except PaymeError as e:
        err = {"code": e.code, "message": e.msg}
        if e.data:
            err["data"] = e.data
        return {"jsonrpc": "2.0", "id": rid, "error": err}, []
    except Exception:
        log.exception("payme internal error")
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32400, "message": {"ru": "Системная ошибка", "uz": "Tizim xatosi", "en": "System error"}}}, []

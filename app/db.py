"""SQLite qatlami (WAL rejimi). Kichik hajm uchun (o'nlab sotuv) sinxron chaqiruvlar yetarli."""
import sqlite3
import threading
from contextlib import contextmanager, closing
from pathlib import Path

from .config import cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id INTEGER UNIQUE NOT NULL,
  username TEXT DEFAULT '',
  tg_name TEXT DEFAULT '',
  full_name TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  region TEXT DEFAULT '',
  registered INTEGER DEFAULT 0,
  blocked INTEGER DEFAULT 0,
  bot_blocked INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  registered_at INTEGER,
  last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  days INTEGER NOT NULL,
  price_uzs INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 0,
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  plan_id INTEGER NOT NULL,
  plan_title TEXT NOT NULL,
  days INTEGER NOT NULL,
  amount_tiyin INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',      -- new | paid | cancelled
  created_at INTEGER NOT NULL,
  paid_at INTEGER,
  license_id INTEGER
);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  payme_id TEXT UNIQUE NOT NULL,
  state INTEGER NOT NULL,                  -- 1 yaratilgan, 2 bajarilgan, -1/-2 bekor
  amount_tiyin INTEGER NOT NULL,
  create_time_ms INTEGER NOT NULL,
  perform_time_ms INTEGER DEFAULT 0,
  cancel_time_ms INTEGER DEFAULT 0,
  reason INTEGER
);
CREATE TABLE IF NOT EXISTS licenses(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  order_id INTEGER,
  plan_id INTEGER,
  plan_title TEXT NOT NULL,
  days INTEGER NOT NULL,
  token TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',      -- new | active | revoked
  source TEXT NOT NULL DEFAULT 'payme',    -- payme | admin
  created_at INTEGER NOT NULL,
  activated_at INTEGER,
  expires_at INTEGER,
  fp_hash TEXT DEFAULT '',
  fp_json TEXT DEFAULT '',
  device_resets_used INTEGER NOT NULL DEFAULT 0,
  last_check_at INTEGER,
  last_ip TEXT DEFAULT '',
  app_version TEXT DEFAULT '',
  note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS device_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_id INTEGER NOT NULL REFERENCES licenses(id),
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  created_at INTEGER NOT NULL,
  resolved_at INTEGER,
  admin_note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reminders_sent(
  license_id INTEGER NOT NULL,
  marker INTEGER NOT NULL,                 -- qolgan kun (0 = tugadi)
  sent_at INTEGER NOT NULL,
  PRIMARY KEY(license_id, marker)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS broadcasts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  audience TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',  -- running | done | cancelled
  created_at INTEGER NOT NULL,
  total INTEGER DEFAULT 0,
  sent INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0,
  finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS broadcast_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  broadcast_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  tg_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | failed
  error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  actor TEXT,
  action TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_pay_order ON payments(order_id);
CREATE INDEX IF NOT EXISTS ix_lic_user ON licenses(user_id);
CREATE INDEX IF NOT EXISTS ix_lic_status ON licenses(status, expires_at);
CREATE INDEX IF NOT EXISTS ix_bi_pending ON broadcast_items(broadcast_id, status);
"""

SEED_PLANS = [
    ("m1", "1 oylik", 30, 1),
    ("m3", "3 oylik", 90, 2),
    ("y1", "1 yillik", 365, 3),
]
SEED_SETTINGS = {
    "reminder_days": "7,3,1",
    "device_resets_per_license": "2",
    "order_timeout_hours": "12",
    "order_warn_minutes": "60",
    "welcome_text": "Assalomu alaykum! Telegram Group Post dasturi litsenziyalarini shu bot orqali sotib olishingiz mumkin.",
    "support_text": "Savol va muammolar bo'yicha admin bilan bog'laning.",
}

_init_lock = threading.Lock()
_inited: set[str] = set()


# Kelajakdagi sxema o'zgarishlari: ro'yxat oxiriga qo'shiladi (hech qachon o'zgartirilmaydi).
# Har element — bitta migratsiya (SQL matni). Mavjud ma'lumotlar saqlanadi.
MIGRATIONS: list[str] = [
    "ALTER TABLE orders ADD COLUMN warned_at INTEGER;",            # 1: to'lov eslatmasi yuborilgan vaqt
    "ALTER TABLE orders ADD COLUMN cancel_reason TEXT DEFAULT '';",  # 2: 'timeout' | 'payme' | ''
    "ALTER TABLE orders ADD COLUMN cancel_notified_at INTEGER;",   # 3: bekor xabari yuborilgan vaqt
]


def _migrate(c: sqlite3.Connection):
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS, start=1):
        if i > ver:
            if sql.strip():
                c.executescript(sql)
            c.execute(f"PRAGMA user_version={i}")


def _init(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _migrate(c)
        for code, title, days, sort in SEED_PLANS:
            c.execute("INSERT OR IGNORE INTO plans(code,title,days,price_uzs,active,sort) VALUES(?,?,?,0,0,?)",
                      (code, title, days, sort))
        for k, v in SEED_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    finally:
        c.close()


def conn() -> sqlite3.Connection:
    path = cfg.DB_PATH
    if path not in _inited:
        with _init_lock:
            if path not in _inited:
                _init(path)
                _inited.add(path)
    c = sqlite3.connect(path, isolation_level=None, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=8000")
    return c


def q(sql: str, args=()) -> list[sqlite3.Row]:
    with closing(conn()) as c:
        return c.execute(sql, args).fetchall()


def one(sql: str, args=()):
    with closing(conn()) as c:
        return c.execute(sql, args).fetchone()


def ex(sql: str, args=()) -> int:
    with closing(conn()) as c:
        return c.execute(sql, args).lastrowid


@contextmanager
def tx():
    """Bitta yozuv tranzaksiyasi (BEGIN IMMEDIATE): ichida c.execute(...) ishlating."""
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        try:
            c.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        c.close()


def get_setting(key: str, default: str = "") -> str:
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r and r["value"] is not None else default


def set_setting(key: str, value: str):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def audit(actor: str, action: str, detail: str = ""):
    from .util import now
    ex("INSERT INTO audit_log(ts,actor,action,detail) VALUES(?,?,?,?)", (now(), actor, action, detail[:500]))

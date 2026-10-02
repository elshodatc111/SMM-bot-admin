import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BOT_MODE"] = "off"
os.environ["BOT_TOKEN"] = ""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("PAYME_KEY", "secret-key")
    monkeypatch.setenv("PAYME_MERCHANT_ID", "merchant123")
    monkeypatch.setenv("ADMIN_TG_ID", "999")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    from app.admin.auth import hash_password
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("admin-password-1", 1000))
    from app import db
    db.conn().close()
    return tmp_path


@pytest.fixture()
def sent(monkeypatch):
    """notify.send/to_admin o'rniga: yuborilgan xabarlar ro'yxati."""
    from app import notify
    box = []

    async def fake_send(tg_id, text, reply_markup=None):
        box.append((tg_id, text))
        return True
    monkeypatch.setattr(notify, "send", fake_send)
    return box


def make_user(tg_id=1001, name="Ali Valiyev", phone="+998901112233", registered=True):
    from app import db
    from app.util import now
    return db.ex("INSERT INTO users(tg_id,username,full_name,phone,region,registered,created_at,registered_at) "
                 "VALUES(?,?,?,?,?,?,?,?)", (tg_id, "ali", name, phone, "Toshkent shahri", 1 if registered else 0, now(), now()))


def make_plan_active(code="m1", price=100000):
    from app import db
    db.ex("UPDATE plans SET price_uzs=?, active=1 WHERE code=?", (price, code))
    return db.one("SELECT * FROM plans WHERE code=?", (code,))

"""Ishga tushirish: noto'g'ri sozlama ilovani qulatmasligi kerak (haqiqiy xato: BASE_URL bo'sh, webhook rejimi)."""
import pytest
from fastapi.testclient import TestClient


def _patch(monkeypatch, calls, fail=None):
    from aiogram import Bot, Dispatcher

    async def delete_webhook(self, *a, **k):
        calls.append("delete_webhook")
        if fail:
            raise fail

    async def set_webhook(self, *a, **k):
        calls.append("set_webhook")

    async def start_polling(self, *a, **k):
        calls.append("polling")

    monkeypatch.setattr(Bot, "delete_webhook", delete_webhook)
    monkeypatch.setattr(Bot, "set_webhook", set_webhook)
    monkeypatch.setattr(Dispatcher, "start_polling", start_polling)


def test_webhook_mode_without_base_url_falls_back_to_polling(env, monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("BOT_MODE", "webhook")
    monkeypatch.setenv("BASE_URL", "")
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
    assert "set_webhook" not in calls and "delete_webhook" in calls


def test_real_webhook_when_configured(env, monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("BOT_MODE", "webhook")
    monkeypatch.setenv("BASE_URL", "https://demo.alwaysdata.net")
    monkeypatch.setenv("WEBHOOK_SECRET", "a" * 32)
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
    assert calls == ["set_webhook"]


def test_bot_error_does_not_crash_app(env, monkeypatch):
    calls = []
    _patch(monkeypatch, calls, fail=RuntimeError("Unauthorized"))
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("BOT_MODE", "polling")
    from app import notify
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert notify.get_bot() is None


def test_migrations_run_once(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    monkeypatch.setattr(db, "MIGRATIONS", ["ALTER TABLE users ADD COLUMN zz TEXT;", "ALTER TABLE users ADD COLUMN yy TEXT;"])
    p = str(tmp_path / "m.sqlite3")
    db._init(p)
    db._init(p)  # ikkinchi marta xato bermasligi kerak
    c = sqlite3.connect(p)
    cols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
    assert "zz" in cols and "yy" in cols
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2

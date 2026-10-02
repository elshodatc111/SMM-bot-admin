"""WEBHOOK_SECRET va SESSION_SECRET uchun tasodifiy qiymatlar: python tools/gen_secrets.py"""
import secrets

print("WEBHOOK_SECRET=" + secrets.token_urlsafe(32).replace("-", "a").replace("_", "b"))
print("SESSION_SECRET=" + secrets.token_hex(32))

"""Admin panel parolining xeshini yaratadi: python tools/make_admin_hash.py"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.admin.auth import hash_password  # noqa: E402

pw = getpass.getpass("Yangi admin parol (kamida 10 belgi): ")
if len(pw) < 10:
    sys.exit("Parol juda qisqa.")
if getpass.getpass("Qayta kiriting: ") != pw:
    sys.exit("Parollar mos kelmadi.")
print("\n.env fayliga yozing:\nADMIN_PASSWORD_HASH=" + hash_password(pw))

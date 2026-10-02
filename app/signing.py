"""Ed25519 imzo: dastur javobning haqiqiyligini ochiq kalit bilan tekshiradi (soxta server o'tmaydi)."""
import base64
import threading
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import cfg

_lock = threading.Lock()
_key: Ed25519PrivateKey | None = None
_key_src = ""


def _load() -> Ed25519PrivateKey:
    global _key, _key_src
    src = cfg.ED25519_PRIVATE_KEY.strip() or str(Path(cfg.DB_PATH).parent)
    with _lock:
        if _key is not None and _key_src == src:
            return _key
        if cfg.ED25519_PRIVATE_KEY.strip():
            _key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(cfg.ED25519_PRIVATE_KEY.strip()))
        else:
            p = Path(cfg.DB_PATH).parent / "ed25519.key"
            if p.exists():
                _key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(p.read_text().strip()))
            else:
                _key = Ed25519PrivateKey.generate()
                p.parent.mkdir(parents=True, exist_ok=True)
                raw = _key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                         serialization.NoEncryption())
                p.write_text(base64.b64encode(raw).decode())
        _key_src = src
        return _key


def sign(payload: str) -> str:
    return base64.b64encode(_load().sign(payload.encode("utf-8"))).decode()


def public_key_b64() -> str:
    pub = _load().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode()

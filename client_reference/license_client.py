"""NAMUNA: Telegram Group Post dasturi litsenziya serveri bilan qanday gaplashishi kerak.
Hozir asosiy loyihaga ulanmagan (keyingi bosqich). Bu fayl server bilan tilni aniq ko'rsatadi va sinovlarda ishlatiladi.

Kerak: pip install httpx cryptography
"""
import base64
import json
import platform
import subprocess
import uuid

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _ps(cmd: str) -> str:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=15)
        return out.stdout.strip()
    except Exception:
        return ""


def collect_fingerprint() -> dict:
    """Windows: MachineGuid + anakart + disk + protsessor + MAC. Server kamida 'machine_guid' yoki 2 ta mosligini talab qiladi."""
    fp = {"mac": f"{uuid.getnode():012x}"}
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                fp["machine_guid"] = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            pass
        fp["board"] = _ps("(Get-CimInstance Win32_BaseBoard).SerialNumber")
        fp["disk"] = _ps("(Get-CimInstance Win32_DiskDrive | Select-Object -First 1).SerialNumber")
        fp["cpu"] = _ps("(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId")
    return {k: v for k, v in fp.items() if v}


def verify(resp: dict, public_key_b64: str) -> dict | None:
    """Javob imzosini tekshiradi. To'g'ri bo'lsa payload (dict), aks holda None."""
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
            base64.b64decode(resp["sig"]), resp["payload"].encode())
        return json.loads(resp["payload"])
    except (InvalidSignature, KeyError, ValueError):
        return None


def activate(server: str, token: str, public_key_b64: str, fp: dict | None = None, client=None, check_only=False):
    """-> (resp, payload|None). resp['code']: ok | invalid_token | revoked | expired | device_mismatch | not_activated ..."""
    c = client or httpx
    r = c.post(f"{server}/api/v1/license/{'check' if check_only else 'activate'}",
               json={"token": token, "fp": fp or collect_fingerprint(), "app_version": "1.0"}, timeout=20)
    resp = r.json()
    return resp, (verify(resp, public_key_b64) if resp.get("payload") else None)

import json

import pytest
from fastapi.testclient import TestClient

from conftest import make_user, make_plan_active

FP = {"machine_guid": "GUID-1", "board": "B1", "disk": "D1", "cpu": "C1", "mac": "AABBCC"}


def issue(days_code="m1"):
    from app import licenses
    uid = make_user()
    p = make_plan_active(days_code, 100000)
    lid = licenses.issue_manual(uid, p["id"])
    return uid, lid, licenses.get_license(lid)["token"]


def test_activate_starts_period_once(env):
    from app import licenses, db
    _, lid, tok = issue("m1")
    r = licenses.activate(tok, FP)
    assert r["ok"]
    lic = licenses.get_license(lid)
    assert lic["status"] == "active" and abs(lic["expires_at"] - lic["activated_at"] - 30 * 86400) < 5
    first_exp = lic["expires_at"]
    # o'sha kompyuterda qayta kiritish (dastur o'chib qayta o'rnatilgan): muddat yangilanmaydi
    r2 = licenses.activate(tok, FP)
    assert r2["ok"] and licenses.get_license(lid)["expires_at"] == first_exp
    # kichik harf/bo'sh joy bilan kiritish ham ishlaydi
    assert licenses.activate(" " + tok.lower().replace("-", " ") + " ", FP)["ok"]


def test_other_device_rejected_and_windows_update_tolerated(env):
    from app import licenses
    _, lid, tok = issue("m1")
    licenses.activate(tok, FP)
    assert licenses.activate(tok, {"machine_guid": "OTHER", "board": "X", "disk": "Y", "cpu": "Z", "mac": "00"})["code"] == "device_mismatch"
    # Windows qayta o'rnatildi: guid o'zgardi, lekin anakart+disk+cpu bir xil
    assert licenses.activate(tok, {**FP, "machine_guid": "NEW-GUID"})["ok"]
    # faqat bitta komponent mos: yetarli emas
    assert licenses.activate(tok, {"machine_guid": "NEWER", "board": "B1", "disk": "q", "cpu": "q", "mac": "q"})["code"] == "device_mismatch"


def test_invalid_revoked_expired(env):
    from app import licenses, db
    _, lid, tok = issue("m1")
    assert licenses.activate("TGP-AAAA-BBBB-CCCC-DDDD", FP)["code"] == "invalid_token"
    assert licenses.activate("garbage", FP)["code"] == "invalid_token"
    assert licenses.activate(tok, {})["code"] == "bad_request"
    licenses.activate(tok, FP)
    db.ex("UPDATE licenses SET expires_at=? WHERE id=?", (1, lid))
    assert licenses.activate(tok, FP)["code"] == "expired"
    licenses.revoke(lid, "test")
    assert licenses.activate(tok, FP)["code"] == "revoked"
    licenses.unrevoke(lid)
    assert licenses.activate(tok, FP)["code"] == "expired"


def test_check_does_not_activate(env):
    from app import licenses
    _, lid, tok = issue("m1")
    assert licenses.activate(tok, FP, create=False)["code"] == "not_activated"
    assert licenses.get_license(lid)["status"] == "new"


def test_http_api_signature(env):
    from app.main import app
    from app.signing import public_key_b64
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "client_reference"))
    import license_client
    _, lid, tok = issue("y1")
    with TestClient(app) as c:
        resp, payload = license_client.activate("", tok, public_key_b64(), FP, client=c)
        assert resp["ok"] and payload and payload["days"] == 365
        resp2, payload2 = license_client.activate("", tok, public_key_b64(), FP, client=c, check_only=True)
        assert payload2["expires_at"] == payload["expires_at"]
        # soxta serverdan kelgan javob (boshqa kalit) rad etiladi
        assert license_client.verify(resp, "A" * 43 + "=") is None
        tampered = dict(resp, payload=resp["payload"].replace("365", "999"))
        assert license_client.verify(tampered, public_key_b64()) is None
        assert c.post("/api/v1/license/activate", content="not json").json()["code"] == "bad_request"


def test_yearly_device_change_flow(env):
    from app import licenses, db
    uid, lid, tok = issue("y1")
    licenses.activate(tok, FP)
    lic = licenses.get_license(lid)
    assert licenses.device_request_state(lic) == (True, "")
    ok, _, rid = licenses.request_device_change(uid, lid)
    assert ok
    assert licenses.request_device_change(uid, lid)[0] is False          # kutilayotgan so'rov bor
    # boshqa foydalanuvchi so'ray olmaydi
    assert licenses.request_device_change(uid + 99, lid)[0] is False
    r = licenses.resolve_device_request(rid, True)
    assert r["status"] == "approved"
    assert licenses.resolve_device_request(rid, True) is None            # ikki marta hal bo'lmaydi
    lic = licenses.get_license(lid)
    assert lic["fp_json"] == "" and lic["device_resets_used"] == 1
    # yangi kompyuterda token qolgan muddat bilan davom etadi
    exp = lic["expires_at"]
    assert licenses.activate(tok, {"machine_guid": "NEW"})["ok"]
    assert licenses.get_license(lid)["expires_at"] == exp
    # ikkinchi marta mumkin, uchinchi marta yo'q
    ok, _, rid = licenses.request_device_change(uid, lid)
    assert ok
    licenses.resolve_device_request(rid, True)
    licenses.activate(tok, {"machine_guid": "NEW2"})
    ok, why, _ = licenses.request_device_change(uid, lid)
    assert not ok and "limit" in why
    # rad etilgan so'rov hisobga kirmaydi
    db.ex("UPDATE licenses SET device_resets_used=0")
    ok, _, rid = licenses.request_device_change(uid, lid)
    licenses.resolve_device_request(rid, False)
    assert licenses.get_license(lid)["device_resets_used"] == 0 and licenses.get_license(lid)["fp_json"] != ""


def test_non_yearly_cannot_request(env):
    from app import licenses
    uid, lid, tok = issue("m1")
    licenses.activate(tok, FP)
    ok, why, _ = licenses.request_device_change(uid, lid)
    assert not ok and "yillik" in why


def test_extend(env):
    from app import licenses
    _, lid, tok = issue("m1")
    licenses.extend(lid, 10)                      # hali aktiv emas: days oshadi
    assert licenses.get_license(lid)["days"] == 40
    licenses.activate(tok, FP)
    before = licenses.get_license(lid)["expires_at"]
    licenses.extend(lid, 5)
    assert licenses.get_license(lid)["expires_at"] == before + 5 * 86400

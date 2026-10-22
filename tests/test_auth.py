"""Accounts and login: scrypt passphrases, signed session cookies, the login rate limit, and solo mode's refusal to be exposed."""
import hashlib
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from conftest import REPO, write_app_config


def test_passphrases_are_scrypt_hashes_with_a_salt(app_shared):
    salt = "00" * 16
    h = app_shared.hash_pw("correct horse battery", salt)
    assert h == hashlib.scrypt(b"correct horse battery", salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()
    assert h != app_shared.hash_pw("correct horse batterz", salt)
    assert h != app_shared.hash_pw("correct horse battery", "11" * 16)
    assert "correct" not in h


def test_stored_hash_is_not_the_passphrase(app_shared):
    with app_shared.db() as c:
        name, pw = app_shared.add_user(c, "Priya", "Priya", "VaultA", admin=False)
        row = c.execute("SELECT salt, hash FROM users WHERE name=?", (name,)).fetchone()
    assert row["hash"] != pw and pw not in row["hash"] and len(row["salt"]) == 32
    assert app_shared.hash_pw(pw, row["salt"]) == row["hash"]


def test_new_accounts_get_distinct_salts_and_passphrases(app_shared):
    with app_shared.db() as c:
        a = app_shared.add_user(c, "ann", vault="VaultA")
        b = app_shared.add_user(c, "bob", vault="VaultA")
        salts = [r["salt"] for r in c.execute("SELECT salt FROM users WHERE name IN ('ann','bob')")]
    assert a[1] != b[1] and salts[0] != salts[1]


def test_session_cookie_is_tamper_proof_and_ends_with_the_passphrase(app_shared):
    exp = int(time.time()) + 3600
    with app_shared.db() as c:
        name, pw = app_shared.add_user(c, "ann", vault="VaultA")
        salt = c.execute("SELECT salt FROM users WHERE name=?", (name,)).fetchone()["salt"]
    good = app_shared.sign(name, exp, salt)
    assert app_shared.user_from_cookie(good)["name"] == "ann"
    assert app_shared.user_from_cookie(good[:-1] + ("0" if good[-1] != "0" else "1")) is None   # forged signature
    assert app_shared.user_from_cookie(good.replace("ann.", "bob.", 1)) is None                  # another name
    assert app_shared.user_from_cookie(app_shared.sign(name, int(time.time()) - 5, salt)) is None  # expired
    with app_shared.db() as c:
        app_shared.set_passphrase(c, name, "a brand new passphrase")
    assert app_shared.user_from_cookie(good) is None                                             # old sessions die


def login(client, name, pw):
    return client.post("/login", content=f"name={name}&pw={pw}", headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)


@pytest.fixture
def shared_client(app_shared, monkeypatch):
    async def no_sleep(_):   # the app sleeps 1.5 s after a failed login to slow guessing; do not wait in tests
        return None
    monkeypatch.setattr(app_shared.asyncio, "sleep", no_sleep)
    with app_shared.db() as c:
        _, pw = app_shared.add_user(c, "ann", vault="VaultA", admin=True)
    return app_shared, TestClient(app_shared.app, base_url="http://testserver"), pw


def test_login_and_gate(shared_client):
    app, client, pw = shared_client
    assert client.get("/api/me").status_code == 401                       # no cookie, no API
    assert client.get("/", follow_redirects=False).status_code == 303     # pages redirect to the login screen
    assert login(client, "ann", "wrong").status_code == 401
    ok = login(client, "ann", pw)
    assert ok.status_code == 303 and "s80_session" in ok.headers["set-cookie"] and "HttpOnly" in ok.headers["set-cookie"]
    assert client.get("/api/me").status_code == 200


def test_login_rate_limit_locks_out_after_eight_failures(shared_client):
    app, client, pw = shared_client
    codes = [login(client, "ann", f"guess-{i}").status_code for i in range(8)]
    assert codes == [401] * 8
    assert login(client, "ann", "guess-9").status_code == 429
    assert login(client, "ann", pw).status_code == 429                    # even the right passphrase waits out the window


def test_rate_limit_is_per_client_address(shared_client):
    app, client, pw = shared_client
    app.CFG["trust_ip_header"] = "x-real-ip"
    for i in range(8):
        client.post("/login", content=f"name=ann&pw=x{i}", headers={"content-type": "application/x-www-form-urlencoded", "x-real-ip": "10.0.0.1"})
    blocked = client.post("/login", content=f"name=ann&pw={pw}", headers={"content-type": "application/x-www-form-urlencoded", "x-real-ip": "10.0.0.1"})
    other = client.post("/login", content=f"name=ann&pw={pw}", headers={"content-type": "application/x-www-form-urlencoded", "x-real-ip": "10.0.0.2"}, follow_redirects=False)
    assert blocked.status_code == 429 and other.status_code == 303


def test_a_locked_account_cannot_log_in(shared_client):
    app, client, pw = shared_client
    with app.db() as c:
        c.execute("UPDATE users SET limits='{\"locked\": true}' WHERE name='ann'")
    # admins are never locked out by limits; make the account a student first
    with app.db() as c:
        c.execute("UPDATE users SET role='student' WHERE name='ann'")
    assert login(client, "ann", pw).status_code == 403


def test_bot_endpoints_need_the_token(shared_client):
    app, client, pw = shared_client
    assert client.get("/api/bot/online").status_code == 401
    (app.DATA / "bot-token").write_text("sekrit\n")
    assert client.get("/api/bot/online", headers={"x-bot-token": "nope"}).status_code == 401
    assert client.get("/api/bot/online", headers={"x-bot-token": "sekrit"}).status_code == 200


def test_solo_mode_needs_no_login(app_solo):
    client = TestClient(app_solo.app)
    assert client.get("/api/me").status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "100.64.0.5", "::"])
def test_solo_mode_refuses_to_be_exposed(app_solo, host):
    with pytest.raises(SystemExit) as e:
        app_solo.bind_check(host)
    assert "no login" in str(e.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_solo_mode_accepts_loopback(app_solo, host):
    os.environ.pop("STELLA80_BIND", None)
    app_solo.bind_check(host)


def test_shared_mode_may_listen_everywhere(app_shared):
    os.environ.pop("STELLA80_BIND", None)
    app_shared.bind_check("0.0.0.0")


def test_starting_the_server_in_solo_mode_on_all_interfaces_exits(tmp_path):
    cfg = write_app_config(tmp_path, "solo", ("VaultA",))
    env = {**os.environ, "STELLA80_CONFIG": str(cfg), "HOST": "0.0.0.0", "PYTHONPATH": ""}
    env.pop("STELLA80_BIND", None)
    r = subprocess.run([sys.executable, str(REPO / "stella80" / "app.py")], env=env, capture_output=True, text=True, timeout=45)
    assert r.returncode != 0 and "no login" in r.stderr

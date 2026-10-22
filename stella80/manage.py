#!/usr/bin/env python3
"""Manage STELLA-80 accounts (shared mode). Reads the same stella80.yaml as the app.

  manage.py list
  manage.py add NAME [--admin] [--vault VAULT] [--display "Shown name"]   new account with a random passphrase
  manage.py passwd NAME [--random]       type a new passphrase, or generate one
  manage.py lock NAME                    remove the passphrase (the account disappears from the login screen)
  manage.py remove NAME                  delete the account, its chats and its study history
  manage.py bot-token                    create the token for the /api/bot/* endpoints (for a chat bot on this machine)

Passphrases are stored as scrypt hashes only. A generated one is printed once; pass it on privately.
An admin can do the same from the web UI: BOARD, ADMIN.
"""
import argparse, getpass, os, re, secrets, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import DATA, DEFAULT_VAULT, VAULTS, add_user, db, init_db, new_passphrase, remove_user, set_passphrase   # noqa: E402


def put(c, name, pw):
    if not set_passphrase(c, name, pw):
        raise SystemExit(f"No such user: {name}")


def ask():
    pw = getpass.getpass("New passphrase: ")
    if len(pw) < 10 or pw != getpass.getpass("Again: "):
        raise SystemExit("Did not match, or shorter than 10 characters.")
    return pw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "add", "passwd", "lock", "remove", "bot-token"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--vault", default=DEFAULT_VAULT, choices=list(VAULTS) or None)
    ap.add_argument("--display")
    ap.add_argument("--random", action="store_true")
    a = ap.parse_args()
    init_db()
    if a.cmd not in ("list", "bot-token") and not a.name:
        ap.error("NAME is required")
    name = re.sub(r"[^a-z0-9_-]", "", (a.name or "").lower())
    with db() as c:
        if a.cmd == "list":
            for r in c.execute("SELECT name, display, vault, role, hash, last_seen FROM users WHERE role!='system' ORDER BY role, name"):
                state = "solo user" if r["hash"] == "!" else "can log in" if r["hash"] else "locked"
                seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"])) if r["last_seen"] else "never"
                print(f"{r['name']:14} {r['display']:14} {r['vault']:20} {r['role']:8} {state:11} last seen {seen}")
        elif a.cmd == "add":
            try:
                name, pw = add_user(c, name, a.display, a.vault, a.admin)
            except ValueError as e:
                raise SystemExit(str(e)) from None
            print(f"Added {name} ({'admin' if a.admin else 'student'}, vault {a.vault}). Passphrase, shown once: {pw}")
        elif a.cmd == "passwd":
            pw = new_passphrase() if a.random else ask()
            put(c, name, pw)
            print(f"New passphrase for {name}: {pw}" if a.random else "Saved.")
        elif a.cmd == "lock":
            c.execute("UPDATE users SET salt=NULL, hash=NULL WHERE name=?", (name,))
            print("Locked.")
        elif a.cmd == "remove":
            print("Removed." if remove_user(c, name) else f"No such user: {name}")
        elif a.cmd == "bot-token":
            f = DATA / "bot-token"
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, (secrets.token_hex(32) + "\n").encode())
            os.close(fd)
            print(f"Token written to {f}. Send it as the X-Bot-Token header.")


if __name__ == "__main__":
    main()

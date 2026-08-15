"""Store the Gmail app password in the Windows Credential Locker.

Run once, then the password lives in the vault and never on disk.

    python scripts\\set_password.py

Must be run from a real interactive terminal. If stdin is redirected or piped,
getpass silently falls back to reading stdin and will capture whatever happens
to be buffered there - which is how command text once ended up in the vault
instead of a password. This refuses to run rather than store garbage.
"""
from __future__ import annotations

import getpass
import json
import os
import sys

from common import CONFIG_PATH, KEYRING_SERVICE, get_password, set_password

# Google app passwords are 16 letters, displayed as four groups of four.
APP_PASSWORD_LEN = 16


def _prompt_hidden(prompt: str) -> str:
    """Read without echoing, straight from the console on Windows."""
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            msvcrt = None
        if msvcrt is not None:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            chars: list[str] = []
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    return "".join(chars)
                if ch == "\003":
                    raise KeyboardInterrupt
                if ch == "\b":
                    if chars:
                        chars.pop()
                elif ch == "\000" or ch == "\xe0":
                    msvcrt.getwch()  # discard the second half of a function key
                else:
                    chars.append(ch)
    return getpass.getpass(prompt)


def _validate(secret: str) -> str:
    cleaned = "".join(secret.split())
    if len(cleaned) != APP_PASSWORD_LEN or not (cleaned.isascii() and cleaned.isalpha()):
        raise SystemExit(
            "That does not look like a Gmail app password. Nothing was stored.\n"
            f"Expected {APP_PASSWORD_LEN} letters (Google shows four groups of four); "
            f"got {len(cleaned)} characters.\n"
            "Copy only the password itself, without any surrounding text."
        )
    return cleaned


def main() -> int:
    if not sys.stdin.isatty():
        raise SystemExit(
            "set_password.py needs an interactive terminal.\n"
            "Open PowerShell or Command Prompt directly and run it there - not\n"
            "through a pipe, redirect, or an embedded terminal that redirects stdin."
        )

    if not CONFIG_PATH.exists():
        print(f"Missing {CONFIG_PATH}", file=sys.stderr)
        return 1

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    address = str(cfg.get("gmail_address") or "").strip()
    if not address:
        print("config.json is missing 'gmail_address'", file=sys.stderr)
        return 1

    print(f"Account:      {address}")
    print(f"Stored under: {KEYRING_SERVICE}")
    if get_password(address):
        print("A password is already stored; continuing replaces it.")

    secret = _validate(_prompt_hidden("App password (input hidden): "))
    set_password(address, secret)

    check = get_password(address)
    if check != secret:
        raise SystemExit("Stored value did not read back correctly.")
    print(f"Stored and verified ({len(secret)} characters).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared paths, config, and logging for the job tracker automation."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "backups"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
CONFIG_PATH = ROOT / "config.json"
DASHBOARD_PATH = ROOT / "dashboard.html"
LAST_RUN_PATH = STATE / "last_run.json"

# Statuses that mean the application is no longer live. Everything else counts
# as active pipeline. Kept here because both workbook.py and dashboard.py need it.
CLOSED_STATUSES = {
    "Rejected",
    "Withdrawn",
    "Ghosted (Post-Engagement)",
    "No Response (6mo+, Application Stage)",
}

# Ordered funnel stages, furthest-reached wins. Used by the dashboard.
FUNNEL_STAGES = [
    ("Applied", None),
    ("Screen", ("Phone Screen Requested", "Phone Screen Completed",
                "Code/Technical Screen Requested", "Code/Technical Screen Completed")),
    ("Interview", ("Onsite/Final Interview Requested", "Onsite/Final Interview Completed")),
    ("Offer", ("Offer",)),
]


def find_workbook() -> Path:
    """Locate the tracker workbook, ignoring backups and Excel lock files."""
    candidates = [
        p for p in ROOT.glob("*.xlsx")
        if not p.name.startswith("~$")
    ]
    if not candidates:
        raise FileNotFoundError(f"No .xlsx workbook found in {ROOT}")
    if len(candidates) > 1:
        # Prefer the most recently modified so a manual "(v4)" wins automatically.
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def is_locked(workbook: Path) -> str | None:
    """Return a reason string if the workbook must not be written, else None."""
    lock = workbook.parent / ("~$" + workbook.name)
    if lock.exists():
        return f"Excel lock file present ({lock.name}) - close the workbook and re-run"
    try:
        with open(workbook, "r+b"):
            pass
    except PermissionError:
        return "workbook is not writable (open in another program?)"
    except OSError as exc:
        return f"workbook is unreadable: {exc}"
    return None


KEYRING_SERVICE = "JobTracker Gmail IMAP"


def _keyring():
    """Optional import: a broken or missing keyring must not strand an
    existing config.json setup, so every caller tolerates None."""
    try:
        import keyring

        return keyring
    except Exception:
        return None


def get_password(address: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(KEYRING_SERVICE, address)
    except Exception:
        return None


def set_password(address: str, password: str) -> None:
    kr = _keyring()
    if kr is None:
        raise SystemExit(
            "keyring is not installed for this interpreter.\n"
            "Run: python -m pip install keyring"
        )
    kr.set_password(KEYRING_SERVICE, address, password)


def load_config() -> dict:
    """Read config.json for the address; take the password from Credential Manager.

    The password lives in the Windows Credential Locker, not on disk. An
    app_password still present in config.json is migrated on first run so an
    existing install keeps working, and can then be deleted.
    """
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"Missing {CONFIG_PATH}.\n"
            'Create it as: {"gmail_address": "you@gmail.com"}\n'
            "Then store the app password with: python scripts\\set_password.py\n"
            "Generate an app password at https://myaccount.google.com/apppasswords (requires 2FA)."
        )
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    address = str(cfg.get("gmail_address") or "").strip()
    if not address:
        raise SystemExit("config.json is missing 'gmail_address'")
    cfg["gmail_address"] = address

    logger = logging.getLogger("jobtracker")
    password = get_password(address)

    if not password:
        legacy = str(cfg.get("app_password") or "").strip()
        if not legacy:
            raise SystemExit(
                f"No app password stored for {address}.\n"
                "Run: python scripts\\set_password.py"
            )
        try:
            set_password(address, legacy.replace(" ", ""))
        except SystemExit:
            raise
        except Exception as exc:
            logger.warning("could not reach Credential Manager: %s", exc)
        else:
            logger.warning(
                "app password copied into Windows Credential Manager - "
                "delete the 'app_password' line from config.json"
            )
        password = legacy

    # Google displays app passwords in groups of four; the spaces are cosmetic.
    cfg["app_password"] = password.replace(" ", "")
    return cfg


def setup_logging(verbose: bool = False) -> logging.Logger:
    LOGS.mkdir(exist_ok=True)
    logger = logging.getLogger("jobtracker")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOGS / "run.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def parse_since(value: str | None, default_days: int = 14) -> dt.date:
    """Accept '30d', '2026-01-15', or None -> the stored watermark, else default."""
    if value:
        m = re.fullmatch(r"(\d+)d", value.strip())
        if m:
            return dt.date.today() - dt.timedelta(days=int(m.group(1)))
        return dt.date.fromisoformat(value.strip())
    if LAST_RUN_PATH.exists():
        try:
            stored = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8")).get("last_run")
            if stored:
                # Re-scan a few days back so a message that arrived mid-run is not skipped.
                return dt.date.fromisoformat(stored) - dt.timedelta(days=2)
        except (json.JSONDecodeError, ValueError):
            pass
    return dt.date.today() - dt.timedelta(days=default_days)


def save_watermark(when: dt.date | None = None) -> None:
    STATE.mkdir(exist_ok=True)
    LAST_RUN_PATH.write_text(
        json.dumps({"last_run": (when or dt.date.today()).isoformat()}, indent=2),
        encoding="utf-8",
    )


def thread_key(thread_id: str) -> str:
    """Normalise a Gmail thread id for comparison.

    The workbook stores 8-char prefixes of real 16-char thread ids (and one row
    contains a literal '...'), so all matching happens on the first 8 chars.
    """
    return re.sub(r"[^0-9a-f]", "", (thread_id or "").lower())[:8]

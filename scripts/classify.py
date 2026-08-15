"""Hand candidate email threads to Claude and get back structured decisions.

The model is used only for judgment (which application is this about, what stage
is it now). All I/O around it is deterministic, and every field it returns is
validated against the Lists sheet before it can reach the workbook.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys

from common import LOGS, setup_logging

CLAUDE_ARGV = [
    "wsl.exe", "-d", "Ubuntu", "--",
    "/home/chase/.local/bin/claude", "-p", "--output-format", "json",
]
TIMEOUT = 600
ACTIONS = {"new_application", "update_existing", "ignore"}

PROMPT = """You are maintaining a job-application tracker. Classify each email thread below.

For every thread, decide exactly one action:
- "new_application": this thread is the first record of an application, or a
  recruiter reaching out, that is NOT already in the existing applications list.
- "update_existing": this thread concerns an application already in the list
  (a rejection, an interview request, a status change). Set "app_id" to the
  matching App ID.
- "ignore": job-adjacent but not trackable - job board alerts, newsletters,
  marketing, account/security notices, or anything you cannot tie to a real
  application.

Match to an existing application when the company matches and the role is the
same or unstated. Prefer "update_existing" over creating a duplicate. If a
company appears in the list but this is clearly a different, newly-submitted
role, use "new_application".

Rules:
- "status" MUST be exactly one of: {statuses}
- "track" MUST be exactly one of: {tracks}
- "source" MUST be exactly one of: {sources}
- Dates are YYYY-MM-DD. "date_applied" is when the application was submitted
  (use the earliest thread message if the email confirms receipt).
  "last_activity_date" is the date of the most recent message in the thread.
- "status_detail" is a short phrase, e.g. "Other candidate selected".
- "notes" is optional and at most one short sentence. Never invent facts.
- Today is {today}.

Return ONLY a JSON array, no prose and no code fence. One object per thread:
[{{"thread_id":"...","action":"...","app_id":null,"company":"...","role":"...",
"track":"...","job_req":"","date_applied":"YYYY-MM-DD","source":"...",
"status":"...","status_detail":"","last_activity_date":"YYYY-MM-DD","notes":""}}]

=== EXISTING APPLICATIONS ===
{existing}

=== EMAIL THREADS TO CLASSIFY ===
{threads}
"""


def _digest(apps: list[dict]) -> str:
    lines = []
    for a in apps:
        applied = a["date_applied"].isoformat() if a.get("date_applied") else ""
        lines.append(f'{a["app_id"]} | {a["company"]} | {a.get("role") or ""} | '
                     f'{a["status"]} | applied {applied}')
    return "\n".join(lines) or "(none)"


def _extract_array(text: str):
    """Pull the JSON array out of the model's reply, fenced or not."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array found in model output")
    return json.loads(text[start:end + 1])


def classify(threads: list[dict], apps: list[dict], lists: dict, logger) -> list[dict]:
    if not threads:
        return []

    prompt = PROMPT.format(
        statuses=" | ".join(lists["status"]),
        tracks=" | ".join(lists["track"]),
        sources=" | ".join(lists["source"]),
        today=dt.date.today().isoformat(),
        existing=_digest(apps),
        threads=json.dumps(threads, indent=1),
    )

    logger.info(f"classifying {len(threads)} threads via claude -p ({len(prompt)} chars)")
    try:
        proc = subprocess.run(
            CLAUDE_ARGV, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"Could not launch wsl.exe: {exc}") from exc
    except subprocess.TimeoutExpired:
        raise SystemExit(f"claude -p timed out after {TIMEOUT}s")

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    raw_path = LOGS / f"classify_{stamp}.json"
    raw_path.write_text(proc.stdout or "", encoding="utf-8")

    if proc.returncode != 0:
        logger.error(f"claude exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}")
        raise SystemExit("classification failed - see logs")

    # --output-format json wraps the reply; older/plain output is the reply itself.
    text = proc.stdout or ""
    try:
        envelope = json.loads(text)
        if isinstance(envelope, dict) and "result" in envelope:
            text = envelope["result"]
    except json.JSONDecodeError:
        pass

    try:
        decisions = _extract_array(text)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error(f"could not parse model output ({exc}); raw reply saved to {raw_path.name}")
        raise SystemExit("classification returned unusable output")

    return validate(decisions, apps, lists, logger)


def validate(decisions, apps: list[dict], lists: dict, logger) -> list[dict]:
    """Drop anything that would put a free-text value into a controlled column."""
    valid_ids = {a["app_id"] for a in apps}
    statuses, tracks, sources = set(lists["status"]), set(lists["track"]), set(lists["source"])
    out = []

    for d in decisions if isinstance(decisions, list) else []:
        if not isinstance(d, dict):
            continue
        tid = str(d.get("thread_id") or "").strip()
        action = str(d.get("action") or "").strip()

        if action not in ACTIONS:
            logger.warning(f"  drop {tid}: unknown action {action!r}")
            continue
        if action == "ignore":
            out.append({"thread_id": tid, "action": "ignore"})
            continue

        status = str(d.get("status") or "").strip()
        if status not in statuses:
            logger.warning(f"  drop {tid}: status {status!r} not in Lists")
            continue

        track = str(d.get("track") or "").strip()
        if track and track not in tracks:
            logger.warning(f"  {tid}: track {track!r} not in Lists, clearing")
            track = ""

        source = str(d.get("source") or "").strip()
        if source and source not in sources:
            logger.warning(f"  {tid}: source {source!r} not in Lists, clearing")
            source = ""

        app_id = str(d.get("app_id") or "").strip()
        if action == "update_existing" and app_id not in valid_ids:
            logger.warning(f"  drop {tid}: update targets unknown app_id {app_id!r}")
            continue

        out.append({
            "thread_id": tid,
            "action": action,
            "app_id": app_id or None,
            "company": str(d.get("company") or "").strip(),
            "role": str(d.get("role") or "").strip(),
            "track": track,
            "job_req": str(d.get("job_req") or "").strip(),
            "date_applied": str(d.get("date_applied") or "").strip(),
            "source": source,
            "status": status,
            "status_detail": str(d.get("status_detail") or "").strip(),
            "last_activity_date": str(d.get("last_activity_date") or "").strip(),
            "notes": str(d.get("notes") or "").strip(),
        })

    dropped = len(decisions) - len(out) if isinstance(decisions, list) else 0
    logger.info(f"classification: {len(out)} usable decisions, {dropped} dropped")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify candidate threads with Claude")
    ap.add_argument("payload", help="JSON file from gmail_fetch.py --out, or '-' for stdin")
    args = ap.parse_args()

    logger = setup_logging()
    from pathlib import Path

    from common import find_workbook
    from workbook import open_workbook, read_applications, read_lists

    raw = sys.stdin.read() if args.payload == "-" else Path(args.payload).read_text(encoding="utf-8")
    threads = json.loads(raw)

    wb = open_workbook(find_workbook())
    decisions = classify(threads, read_applications(wb), read_lists(wb), logger)
    print(json.dumps(decisions, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

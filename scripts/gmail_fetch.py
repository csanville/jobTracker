"""Fetch candidate job-related email threads over IMAP (stdlib only).

Two passes: fetch headers for everything in the window (cheap), keep what looks
job-related, then pull full bodies only for the survivors. Threads already
recorded in the workbook are dropped before anything reaches the model.
"""
from __future__ import annotations

import argparse
import email
import html
import imaplib
import json
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from common import load_config, parse_since, setup_logging, thread_key

IMAP_HOST = "imap.gmail.com"
ALL_MAIL = '"[Gmail]/All Mail"'
BODY_LIMIT = 2000
BATCH = 200

# Anything a job application, recruiter, or ATS is likely to say.
RELEVANT = re.compile(
    r"applicat|applied|apply|thank you for (your )?(interest|applying|time)|"
    # ATS rejections often have a keyword-free subject ("Thank You from Acme").
    r"thank(s| you)\b[^.]{0,20}\bfrom\b|"
    r"interview|recruit|candidate|hiring|position|job\s*req|requisition|"
    r"screening|screen|offer|talent|resume|cv\b|"
    r"regret|unfortunately|not (be )?(moving|proceeding)|moving forward|"
    # Match the vendor's sending domain, not its website: Greenhouse mails from
    # us.greenhouse-mail.io, so anchoring on "greenhouse.io" missed every one.
    r"greenhouse|lever\.co|myworkday|workday|ashbyhq|icims|taleo|"
    r"smartrecruiters|jobvite|bamboohr|successfactors|brassring|workable|rippling",
    re.I,
)

# Job-board blasts and newsletters that would otherwise flood the classifier.
NOISE = re.compile(
    r"job alert|jobs for you|new jobs|recommended (jobs|for you)|jobs matching|"
    r"jobs you may|top job|hiring now near|apply now|unsubscribe from job|"
    r"newsletter|digest|webinar|career fair|salary report",
    re.I,
)


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return raw.strip()


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def body_text(msg) -> str:
    plain, rich = [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except (TypeError, ValueError):
            continue
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        (plain if ctype == "text/plain" else rich).append(decoded)
    text = "\n".join(plain) if plain else strip_html("\n".join(rich))
    # Trim quoted history so the model sees the new content, not the whole chain.
    text = re.split(r"\nOn .{0,80}wrote:|\n-{3,} ?Original Message|\n_{10,}", text)[0]
    return text.strip()[:BODY_LIMIT]


def _parse_thrid(blob: bytes) -> str | None:
    m = re.search(rb"X-GM-THRID\s+(\d+)", blob)
    return format(int(m.group(1)), "x") if m else None


def fetch_candidates(since, known_keys: set[str], logger, limit: int | None = None) -> list[dict]:
    cfg = load_config()
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(cfg["gmail_address"], cfg["app_password"])
    except imaplib.IMAP4.error as exc:
        raise SystemExit(
            f"Gmail login failed: {exc}\n"
            "Check config.json. The app password must be from "
            "https://myaccount.google.com/apppasswords and 2FA must be enabled."
        ) from exc

    try:
        imap.select(ALL_MAIL, readonly=True)
        criterion = since.strftime("%d-%b-%Y")
        typ, data = imap.search(None, "SINCE", criterion)
        if typ != "OK":
            raise SystemExit(f"IMAP search failed: {typ}")
        seq = data[0].split()
        logger.info(f"IMAP: {len(seq)} messages since {criterion}")
        if not seq:
            return []

        # Pass 1 - headers only.
        interesting: list[tuple[bytes, str]] = []
        for i in range(0, len(seq), BATCH):
            chunk = b",".join(seq[i:i + BATCH]).decode()
            typ, resp = imap.fetch(
                chunk, "(X-GM-THRID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            )
            if typ != "OK":
                continue
            for item in resp:
                if not isinstance(item, tuple):
                    continue
                thrid = _parse_thrid(item[0])
                if not thrid:
                    continue
                hdr = email.message_from_bytes(item[1])
                subject = _decode(hdr.get("Subject"))
                sender = _decode(hdr.get("From"))
                haystack = f"{sender} {subject}"
                if NOISE.search(haystack) or not RELEVANT.search(haystack):
                    continue
                if thread_key(thrid) in known_keys:
                    continue
                num = re.match(rb"(\d+)", item[0])
                if num:
                    interesting.append((num.group(1), thrid))

        logger.info(f"IMAP: {len(interesting)} messages look job-related and are untracked")
        if limit:
            interesting = interesting[:limit]
        if not interesting:
            return []

        # Pass 2 - full bodies for survivors only, grouped by thread.
        threads: dict[str, dict] = {}
        nums = [n for n, _ in interesting]
        thrid_by_num = {n: t for n, t in interesting}
        for i in range(0, len(nums), 50):
            chunk = b",".join(nums[i:i + 50]).decode()
            typ, resp = imap.fetch(chunk, "(RFC822)")
            if typ != "OK":
                continue
            for item in resp:
                if not isinstance(item, tuple):
                    continue
                num = re.match(rb"(\d+)", item[0])
                if not num:
                    continue
                thrid = thrid_by_num.get(num.group(1))
                if not thrid:
                    continue
                msg = email.message_from_bytes(item[1])
                try:
                    when = parsedate_to_datetime(msg.get("Date"))
                    when_s = when.date().isoformat()
                except (TypeError, ValueError):
                    when_s = ""
                entry = threads.setdefault(
                    thrid, {"thread_id": thrid, "messages": []}
                )
                entry["messages"].append({
                    "from": _decode(msg.get("From")),
                    "to": _decode(msg.get("To")),
                    "subject": _decode(msg.get("Subject")),
                    "date": when_s,
                    "body": body_text(msg),
                })

        for t in threads.values():
            t["messages"].sort(key=lambda m: m["date"])
        logger.info(f"IMAP: grouped into {len(threads)} candidate threads")
        return list(threads.values())
    finally:
        try:
            imap.close()
        except imaplib.IMAP4.error:
            pass
        imap.logout()


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch candidate job emails over IMAP")
    ap.add_argument("--since", help="'30d' or 'YYYY-MM-DD' (default: stored watermark)")
    ap.add_argument("--limit", type=int, help="cap candidate messages (testing)")
    ap.add_argument("--dry-run", action="store_true", help="print a summary, write nothing")
    ap.add_argument("--out", help="write the candidate JSON here")
    args = ap.parse_args()

    logger = setup_logging()
    from common import find_workbook
    from workbook import known_thread_keys, open_workbook, read_applications

    wb = open_workbook(find_workbook())
    known = known_thread_keys(read_applications(wb))
    logger.info(f"{len(known)} thread ids already tracked in the workbook")

    since = parse_since(args.since)
    threads = fetch_candidates(since, known, logger, limit=args.limit)

    if args.dry_run:
        for t in threads:
            first = t["messages"][0]
            print(f"  {t['thread_id']}  {first['date']}  {first['subject'][:70]}")
            print(f"      from: {first['from'][:70]}  ({len(t['messages'])} msg)")
        print(f"\n{len(threads)} candidate threads (nothing written)")
        return 0

    payload = json.dumps(threads, indent=2)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(payload, encoding="utf-8")
        logger.info(f"wrote {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

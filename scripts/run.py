"""Orchestrate a full sync: fetch mail -> classify -> update workbook -> rebuild dashboard.

Safe to re-run. Thread-id dedup makes it idempotent, so a second run right after
the first adds nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    find_workbook, is_locked, parse_since, save_watermark, setup_logging,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync the job tracker from Gmail")
    ap.add_argument("--since", help="'30d' or 'YYYY-MM-DD' (default: stored watermark)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and classify, print decisions, write nothing")
    ap.add_argument("--no-mail", action="store_true",
                    help="skip Gmail; just regenerate the dashboard from the workbook")
    ap.add_argument("--workbook", help="operate on this file instead of the detected one")
    ap.add_argument("--limit", type=int, help="cap candidate messages (testing)")
    args = ap.parse_args()

    logger = setup_logging()
    from dashboard import build as build_dashboard  # noqa: E402
    from gmail_fetch import fetch_candidates  # noqa: E402
    from workbook import (  # noqa: E402
        apply_decisions, backup, expire_stale, known_thread_keys, open_workbook,
        read_applications, read_lists, regenerate_active_pipeline,
        regenerate_company_history,
    )

    path = Path(args.workbook) if args.workbook else find_workbook()
    logger.info(f"--- sync start: {path.name} ---")

    if not args.dry_run:
        reason = is_locked(path)
        if reason:
            logger.error(f"cannot write: {reason}")
            return 2

    wb = open_workbook(path)
    apps = read_applications(wb)
    lists = read_lists(wb)
    logger.info(f"workbook: {len(apps)} applications, {len(lists['status'])} statuses in Lists")

    summary = {"added": 0, "updated": 0, "skipped": 0}
    if not args.no_mail:
        since = parse_since(args.since)
        threads = fetch_candidates(since, known_thread_keys(apps), logger, limit=args.limit)
        if threads:
            from classify import classify  # noqa: E402
            decisions = classify(threads, apps, lists, logger)
            if args.dry_run:
                print(json.dumps(decisions, indent=2))
            else:
                summary = apply_decisions(wb, decisions, logger)
        else:
            logger.info("no new candidate threads")

    # After this run's mail is applied, so a reply that just arrived keeps a row open.
    expired = expire_stale(wb, logger)

    if args.dry_run:
        logger.info("dry run: nothing written")
        return 0

    dest = backup(path)
    logger.info(f"backup: {dest.name}")

    n_active = regenerate_active_pipeline(wb)
    n_companies = regenerate_company_history(wb)
    wb.save(path)
    logger.info(f"workbook saved: +{summary['added']} new, {summary['updated']} updated, "
                f"{summary['skipped']} skipped, {expired} expired | Active Pipeline {n_active} rows, "
                f"Company History {n_companies} rows")

    wb2 = open_workbook(path)
    out, metrics = build_dashboard(read_applications(wb2))
    logger.info(f"dashboard: {out.name} ({metrics['total']} applications, "
                f"{metrics['active']} active, {metrics['rejection_rate']:.0f}% rejected)")

    if not args.no_mail:
        save_watermark()
    logger.info("--- sync done ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

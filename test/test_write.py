"""Exercise the workbook write path against a throwaway copy."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from common import find_workbook, setup_logging  # noqa: E402
from workbook import (  # noqa: E402
    apply_decisions, known_thread_keys, open_workbook, read_applications,
    regenerate_active_pipeline, regenerate_company_history,
)

DST = ROOT / "test" / "write-test.xlsx"

DECISIONS = [
    # brand new company
    {"thread_id": "1a2b3c4d5e6f7890", "action": "new_application", "app_id": None,
     "company": "Acme Robotics", "role": "Senior Software Engineer", "track": "SWE",
     "job_req": "REQ-9001", "date_applied": "2026-07-20", "source": "Direct Application",
     "status": "Awaiting Response", "status_detail": "", "last_activity_date": "2026-07-20",
     "notes": "Applied via careers page"},
    # repeat at a company that already exists -> should get Attempt # 5 and a prior link
    {"thread_id": "aaaa1111bbbb2222", "action": "new_application", "app_id": None,
     "company": "Docusign", "role": "Staff Engineer", "track": "SWE", "job_req": "",
     "date_applied": "2026-07-21", "source": "Referral", "status": "Phone Screen Requested",
     "status_detail": "Recruiter proposed times", "last_activity_date": "2026-07-22",
     "notes": "Referred by a former colleague"},
    # update an existing row
    {"thread_id": "cccc3333dddd4444", "action": "update_existing", "app_id": "APP-005",
     "company": "", "role": "", "track": "", "job_req": "", "date_applied": "",
     "source": "", "status": "Rejected", "status_detail": "Other candidate selected",
     "last_activity_date": "2026-07-23", "notes": "Closed out"},
    # already-tracked thread -> must be skipped
    {"thread_id": "19bc37d2", "action": "new_application", "app_id": None,
     "company": "Boeing", "role": "Duplicate", "track": "SWE", "job_req": "",
     "date_applied": "2026-07-01", "source": "Direct Application",
     "status": "Awaiting Response", "status_detail": "", "last_activity_date": "2026-07-01",
     "notes": ""},
    {"thread_id": "ffff5555", "action": "ignore"},
]


def main():
    # Whichever workbook is in the repo root; never written to directly.
    shutil.copy2(find_workbook(), DST)
    logger = setup_logging()

    wb = open_workbook(DST)
    before = read_applications(wb)
    prose_before = {}
    ws = wb["Company History"]
    for r in range(2, ws.max_row + 1):
        name = ws[f"A{r}"].value
        if name:
            prose_before[str(name).strip().lower()] = (
                ws[f"F{r}"].value, ws[f"G{r}"].value, ws[f"H{r}"].value)

    print(f"\n--- pass 1 ({len(before)} rows before) ---")
    s1 = apply_decisions(wb, DECISIONS, logger)
    n_active = regenerate_active_pipeline(wb)
    n_comp = regenerate_company_history(wb)
    wb.save(DST)
    print(f"summary: {s1} | active={n_active} companies={n_comp}")

    wb = open_workbook(DST)
    after = read_applications(wb)
    new_rows = [a for a in after if a["app_id"] not in {b["app_id"] for b in before}]
    print("\nnew rows:")
    for a in new_rows:
        print(f"  {a['app_id']} | {a['company']} | {a['role']} | {a['status']} | "
              f"attempt={a['attempt']} prior={a['prior_attempt']!r} | thread={a['thread_id']}")

    app005 = next(a for a in after if a["app_id"] == "APP-005")
    print(f"\nAPP-005 after update: status={app005['status']!r} "
          f"detail={app005['status_detail']!r} last={app005['last_activity_date']} "
          f"threads={app005['thread_id']!r}")

    ws = wb["Applications"]
    for a in new_rows:
        print(f"  col N formula on row {a['row']}: {ws['N%d' % a['row']].value!r}")

    # prose preservation
    ws = wb["Company History"]
    lost = []
    for r in range(2, ws.max_row + 1):
        name = ws[f"A{r}"].value
        if not name:
            continue
        key = str(name).strip().lower()
        if key in prose_before:
            now = (ws[f"F{r}"].value, ws[f"G{r}"].value, ws[f"H{r}"].value)
            if now != prose_before[key]:
                lost.append((name, prose_before[key], now))
    print(f"\nCompany History prose changed for {len(lost)} existing companies")
    for name, was, now in lost[:5]:
        print(f"  {name}: {was} -> {now}")

    # idempotency
    print("\n--- pass 2 (same decisions again) ---")
    s2 = apply_decisions(wb, DECISIONS, logger)
    print(f"summary: {s2}")
    assert s2["added"] == 0, f"NOT IDEMPOTENT: added {s2['added']} rows on rerun"
    print("\nOK: rerun added 0 rows")

    # active pipeline should include the two new active rows
    ws = wb["Active Pipeline"]
    print(f"Active Pipeline rows: {ws.max_row - 1}")


if __name__ == "__main__":
    main()

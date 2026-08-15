# Job Tracker

Reads a Gmail mailbox over IMAP, asks Claude to classify each job-related thread,
and keeps an Excel tracker plus a self-contained HTML dashboard up to date.

A full sync is idempotent — threads are deduplicated by Gmail thread id, so
re-running immediately adds nothing.

```
gmail_fetch  ->  classify  ->  workbook  ->  dashboard
 (IMAP)         (claude -p)    (openpyxl)    (plotly)
```

## Layout

| Path | Purpose |
| --- | --- |
| `scripts/run.py` | Orchestrates the full sync; the entry point. |
| `scripts/gmail_fetch.py` | Pulls candidate threads from Gmail over IMAP. |
| `scripts/classify.py` | Sends threads to `claude -p`, validates the decisions. |
| `scripts/workbook.py` | Reads/writes the `.xlsx` tracker, regenerates derived sheets. |
| `scripts/dashboard.py` | Renders `dashboard.html` with an inlined Plotly bundle. |
| `scripts/common.py` | Shared paths, config, credential access, logging. |
| `scripts/set_password.py` | Stores the Gmail app password in the OS credential vault. |
| `run.bat` | Windows Task Scheduler entry point; args pass through to `run.py`. |
| `test/` | Pytest suite and a synthetic thread fixture. |

`scripts/plotly.min.js` is a vendored copy of Plotly, inlined into the dashboard
so the generated HTML works with no network access.

## Setup

1. Install the dependencies:

   ```
   python -m pip install -r requirements.txt
   ```

2. Create `config.json` from the example and set your own address:

   ```
   cp config.example.json config.json
   ```

3. Enable 2FA on the Google account and generate an app password at
   <https://myaccount.google.com/apppasswords>, then store it in the OS
   credential vault (it is never written to disk):

   ```
   python scripts/set_password.py
   ```

4. Put the tracker workbook (`*.xlsx`) in the repository root. It is discovered
   automatically; if several are present, the most recently modified wins.

## Running

```
python scripts/run.py              # full sync since the stored watermark
python scripts/run.py --since 30d  # or a YYYY-MM-DD date
python scripts/run.py --dry-run    # fetch and classify, print decisions, write nothing
python scripts/run.py --no-mail    # rebuild the dashboard from the workbook only
```

Every write is preceded by a timestamped backup into `backups/`, and the sync
refuses to run if the workbook is open in Excel.

## Tests

`test/test_write.py` copies the workbook to `test/write-test.xlsx` and exercises
the write path against the copy, asserting that a repeated run stays idempotent.
It needs a workbook in the repository root.

```
python test/test_write.py
```

## Machine-specific settings

`CLAUDE_ARGV` at the top of `scripts/classify.py` hardcodes how the Claude CLI is
launched — on this setup, through `wsl.exe` at an absolute path under the user's
home directory. Adjust it for your own machine.

## What is not in this repository

The tracker's contents are personal, so `.gitignore` keeps them local:
the `*.xlsx` workbook and its `backups/`, the generated `dashboard.html`, the
`logs/` (which quote email subjects, bodies, and company names), the `state/`
watermark, and `config.json` itself. No credentials are stored in the repository —
the app password lives in the OS credential vault.

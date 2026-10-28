#!/usr/bin/env python3
"""
Smart wrapper for sender.py.
Task Scheduler calls this instead of sender.py directly.
It opens outreach.xlsx headless (openpyxl, no Excel GUI, no console)
and only launches sender.py when there is a due email to send.
"""

import sys
import subprocess
import openpyxl
from datetime import datetime
import pytz

OUTREACH_XLSX = r"F:\_PROJECTS\EMails\outreach.xlsx"
SENDER_SCRIPT = r"F:\_PROJECTS\EMails\sender.py"


def parse_utc(utc_str):
    """Parse UTC string from Excel (handles Z, +00:00, naive)"""
    if not utc_str:
        return None
    s = str(utc_str).strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def has_due_emails():
    """Return True if any row needs sending."""
    try:
        wb = openpyxl.load_workbook(OUTREACH_XLSX)
        ws = wb["Queue"]
    except Exception:
        return False

    now_utc = datetime.now(pytz.UTC)

    for row in range(2, ws.max_row + 1):
        status = ws.cell(row=row, column=19).value      # status
        approve = ws.cell(row=row, column=20).value     # approve
        send_after = ws.cell(row=row, column=21).value  # send_after_utc

        if status != 'initial' or approve != 'y' or not send_after:
            continue

        send_utc = parse_utc(send_after)
        if send_utc and send_utc <= now_utc:
            return True

    return False


def main():
    if not has_due_emails():
        print("No due emails. Exiting.")
        return 0

    print("Due emails found. Running sender.py...")
    result = subprocess.run([sys.executable, SENDER_SCRIPT])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

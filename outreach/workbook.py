"""Workbook schema, styling, and validation for outreach.xlsx.

This module knows only about *shape* (sheets, columns, formatting). The Store
in ``store.py`` owns reading/writing/backups.
"""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

QUEUE_COLUMNS = [
    "id",
    "company",
    "website",
    "country",
    "region_priority",
    "company_type",
    "contact_name",
    "contact_role",
    "to_email",
    "email_source",
    "email_confidence",
    "subject",
    "headline",
    "body",
    "attachment",
    "evidence",
    "template_variant",
    "status",
    "approve",
    "send_after_utc",
    "local_send_time",
    "recipient_tz",
    "sent_at",
    "message_id",
    "thread_key",
    "attempts",
    "addresses_tried",
    "last_error",
    "followup_due",
    "notes",
    "draft_hash",
    "reply_sentiment",
    "is_resend",
]

CONTACTED_COLUMNS = ["domain", "email", "company", "first_contacted", "source", "outcome"]

BAD_COLUMNS = ["email", "domain", "company", "reason", "status_code", "added_at"]

SUPPRESSED_COLUMNS = ["email", "domain", "company", "reason", "added_at", "source"]

LOG_COLUMNS = ["timestamp", "command", "level", "message"]

SUMMARY_COLUMNS = [
    "date",
    "researched",
    "drafted",
    "approved",
    "sent",
    "bounced",
    "resent",
    "replies",
    "followups_sent",
    "gave_up",
    "errors",
    "search_calls",
    "notes",
]

FREEHIRE_COLUMNS = [
    "slug",
    "title",
    "company",
    "location",
    "work_mode",
    "url",
    "status",
    "applied",
    "first_seen",
    "last_alert_at",
    "alerts",
    "applied_at",
    "closed_at",
    "notes",
    "category",
]

SHEETS = {
    "Queue": QUEUE_COLUMNS,
    "Contacted": CONTACTED_COLUMNS,
    "BadAddresses": BAD_COLUMNS,
    "Suppressed": SUPPRESSED_COLUMNS,
    "Log": LOG_COLUMNS,
    "Summary": SUMMARY_COLUMNS,
    "Freehire": FREEHIRE_COLUMNS,
}

STATUSES = [
    "pending_review",
    "approved",
    "scheduled",
    "sending",
    "sent",
    "delivered_assumed",
    "bounced_soft",
    "bounced_hard",
    "needs_new_address",
    "replied",
    "auto_reply",
    "followup_pending",
    "followup_sent",
    "skipped",
    "gave_up",
    "suppressed",
    "error",
]

APPROVE_OPTIONS = ["", "y", "skip"]

COMPANY_TYPES = ["studio", "agency", "product", "corporation"]

EMAIL_CONFIDENCE = ["high", "medium", "low"]

REGIONS = [
    "Latin America",
    "Europe (tier 2/3)",
    "Middle East",
    "China",
    "Japan",
    "Malaysia",
    "Singapore",
    "Indonesia",
    "United States",
    "Europe (tier 1)",
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)
POSITIVE_FILL = PatternFill("solid", fgColor="C6EFCE")
NEGATIVE_FILL = PatternFill("solid", fgColor="FFC7CE")

WRAP_COLUMNS = {"body", "evidence", "notes", "message", "last_error", "reason", "subject"}
WIDTHS = {
    "id": 22,
    "company": 24,
    "website": 30,
    "country": 16,
    "region_priority": 16,
    "company_type": 13,
    "contact_name": 18,
    "contact_role": 20,
    "to_email": 26,
    "email_source": 30,
    "email_confidence": 13,
    "subject": 40,
    "headline": 34,
    "body": 70,
    "attachment": 20,
    "evidence": 45,
    "template_variant": 16,
    "status": 17,
    "approve": 9,
    "send_after_utc": 21,
    "local_send_time": 21,
    "recipient_tz": 18,
    "sent_at": 21,
    "message_id": 40,
    "thread_key": 30,
    "attempts": 9,
    "addresses_tried": 30,
    "last_error": 40,
    "followup_due": 21,
    "notes": 40,
    "draft_hash": 20,
    "reply_sentiment": 14,
    "timestamp": 21,
    "command": 14,
    "level": 9,
    "message": 90,
    "domain": 26,
    "email": 30,
    "first_contacted": 21,
    "source": 16,
    "outcome": 16,
    "reason": 30,
    "status_code": 14,
    "added_at": 21,
    "date": 12,
    "researched": 11,
    "drafted": 9,
    "approved": 10,
    "sent": 8,
    "bounced": 9,
    "resent": 8,
    "replies": 8,
    "followups_sent": 15,
    "gave_up": 9,
    "errors": 8,
    "search_calls": 12,
    "slug": 44,
    "work_mode": 12,
    "url": 45,
    "applied": 9,
    "alerts": 8,
    "applied_at": 21,
    "closed_at": 21,
    "category": 11,
}


def create_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    for name, columns in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(columns)
    return wb


def ensure_schema(wb: Workbook) -> bool:
    """Add missing sheets/columns to an existing workbook. Returns True if changed."""
    changed = False
    for name, columns in SHEETS.items():
        if name not in wb.sheetnames:
            wb.create_sheet(name).append(columns)
            changed = True
            continue
        ws = wb[name]
        existing = [c.value for c in ws[1]] if ws.max_row >= 1 else []
        existing = [c for c in existing if c]
        missing = [c for c in columns if c not in existing]
        if missing:
            for col in missing:
                ws.cell(row=1, column=ws.max_column + 1, value=col)
            changed = True
    return changed


def style_sheet(ws, columns: list[str]) -> None:
    ws.freeze_panes = "A2"
    if ws.max_row >= 1 and ws.max_column >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(ws.max_row, 1)}"
    for idx, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = WIDTHS.get(name, 16)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            header = columns[cell.column - 1] if cell.column - 1 < len(columns) else ""
            if header in WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.alignment = Alignment(vertical="top")


def apply_validations(wb: Workbook) -> None:
    queue = wb["Queue"]
    queue.data_validations.dataValidation = []

    status_len = max(1, min(len(",".join(STATUSES)), 255))
    status_formula = f'"{",".join(STATUSES)}"' if status_len else None
    if status_formula:
        dv_status = DataValidation(
            type="list", formula1=status_formula, allow_blank=True, showDropDown=False
        )
        dv_status.error = "Pick a valid status."
        dv_status.promptTitle = "Status"
        queue.add_data_validation(dv_status)
        dv_status.add(f"R2:R1048576")

    dv_approve = DataValidation(
        type="list", formula1='"y,skip"', allow_blank=True, showDropDown=False
    )
    dv_approve.error = "Type y to approve or skip to reject."
    dv_approve.promptTitle = "Approve"
    queue.add_data_validation(dv_approve)
    dv_approve.add("R2:R1048576")

    dv_type = DataValidation(
        type="list", formula1=f'"{",".join(COMPANY_TYPES)}"', allow_blank=True
    )
    queue.add_data_validation(dv_type)
    dv_type.add("F2:F1048576")

    dv_conf = DataValidation(
        type="list", formula1=f'"{",".join(EMAIL_CONFIDENCE)}"', allow_blank=True
    )
    queue.add_data_validation(dv_conf)
    dv_conf.add("K2:K1048576")


def build_workbook() -> Workbook:
    """Create a fully-formed workbook with headers, styling and dropdowns."""
    wb = create_workbook()
    for name, columns in SHEETS.items():
        style_sheet(wb[name], columns)
    apply_validations(wb)
    return wb


def restyle_all(wb: Workbook) -> None:
    for name, columns in SHEETS.items():
        if name in wb.sheetnames:
            style_sheet(wb[name], columns)
    apply_validations(wb)

"""`import-contacted`: load an existing contacted list into the history sheet.

Accepts CSV or XLSX. Recognises columns case-insensitively: email, company,
domain, website. Any missing column is derived (domain from website/email).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .. import util

EMAIL_KEYS = ["email", "e-mail", "mail", "email address", "contact email", "to_email"]
COMPANY_KEYS = ["company", "company name", "organization", "organisation", "name"]
DOMAIN_KEYS = ["domain", "website", "url", "site", "web"]
OUTCOME_KEYS = ["outcome", "status", "notes", "note", "result"]


def _pick(row: dict, keys: list[str]) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for key in keys:
        if key in lowered and lowered[key] not in (None, ""):
            return str(lowered[key]).strip()
    return ""


def _read_csv(path: Path) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Could not decode {path}")


def _read_xlsx(path: Path) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h) if h is not None else "" for h in rows[0]]
    out = []
    for row in rows[1:]:
        out.append({headers[i]: row[i] for i in range(min(len(headers), len(row)))})
    return out


def run(ctx) -> int:
    path = Path(ctx.args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        raw_rows = _read_xlsx(path)
    elif suffix in (".csv", ".txt", ".tsv"):
        raw_rows = _read_csv(path)
    else:
        print("Unsupported file type. Use .csv or .xlsx.")
        return 1

    store = ctx.store
    existing_domains = store.contacted_domains()
    existing_emails = store.contacted_emails()

    added = 0
    skipped = 0
    for raw in raw_rows:
        if not any(v for v in raw.values()):
            continue
        email = _pick(raw, EMAIL_KEYS).lower()
        company = _pick(raw, COMPANY_KEYS)
        domain = _pick(raw, DOMAIN_KEYS)
        outcome = _pick(raw, OUTCOME_KEYS) or "imported"

        if domain:
            domain = util.domain_of(domain)
        if not domain and email:
            domain = util.domain_of(email)
        if not domain and not email:
            skipped += 1
            continue

        rdomain = util.registrable_domain(domain)
        if email and email in existing_emails:
            skipped += 1
            continue
        if rdomain and rdomain in existing_domains:
            skipped += 1
            continue

        store.contacted_add({
            "domain": rdomain or domain,
            "email": email,
            "company": company,
            "first_contacted": util.to_iso(util.now_utc()),
            "source": "imported",
            "outcome": outcome,
        })
        if email:
            existing_emails.add(email)
        if rdomain:
            existing_domains.add(rdomain)
        added += 1

    ctx.logger.info("import-contacted: %d added, %d skipped from %s", added, skipped, path.name)
    print(f"Imported {added} contact(s); skipped {skipped} (duplicates/empty).")
    return 0

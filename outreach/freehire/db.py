"""SQLite state for watched freehire jobs.

One row per public_slug. Delivery state (alerts, next_due_at) lives here so a
failed alert never advances anything and the next cycle simply retries.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .. import util

STATUS_BASELINE = "baseline"
STATUS_WATCHING = "watching"
STATUS_APPLIED = "applied"
STATUS_SKIPPED = "skipped"
STATUS_CLOSED = "closed"

JOB_FIELDS = ("slug", "title", "company", "url", "location", "work_mode")


class JobsDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                slug TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                work_mode TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'watching',
                alerts INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL DEFAULT '',
                last_alert_at TEXT,
                next_due_at TEXT,
                applied_at TEXT,
                closed_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.conn.commit()

    # -- reads ---------------------------------------------------------------
    def is_empty(self) -> bool:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
        return int(row["n"]) == 0

    def get(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def all_rows(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM jobs ORDER BY first_seen")]

    def slugs(self) -> set[str]:
        return {r["slug"] for r in self.conn.execute("SELECT slug FROM jobs")}

    def by_status(self, status: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM jobs WHERE status = ?", (status,))
        return [dict(r) for r in rows]

    def undigested(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE status = ? AND alerts = 0 ORDER BY first_seen",
            (STATUS_WATCHING,),
        )
        return [dict(r) for r in rows]

    # -- writes --------------------------------------------------------------
    def insert(self, job: dict, status: str, now_iso: str, next_due_iso: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (slug, title, company, url, location, work_mode,"
            " status, first_seen, next_due_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.get("public_slug") or "",
                job.get("title") or "",
                job.get("company") or "",
                job.get("url") or "",
                job.get("location") or "",
                job.get("work_mode") or "",
                status,
                now_iso,
                next_due_iso,
            ),
        )
        self.conn.commit()

    def update(self, slug: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {cols} WHERE slug = ?", (*fields.values(), slug))
        self.conn.commit()

    def set_status(self, slug: str, status: str, now_iso: str) -> bool:
        fields: dict[str, Any] = {"status": status, "next_due_at": None}
        if status == STATUS_APPLIED:
            fields["applied_at"] = now_iso
        cur = self.conn.execute(
            f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE slug = ?",
            (*fields.values(), slug),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def record_alert(self, slug: str, now_iso: str, next_due_iso: str | None) -> None:
        self.conn.execute(
            "UPDATE jobs SET alerts = alerts + 1, last_alert_at = ?, next_due_at = ?"
            " WHERE slug = ?",
            (now_iso, next_due_iso, slug),
        )
        self.conn.commit()

    # -- meta ----------------------------------------------------------------
    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

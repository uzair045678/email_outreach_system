"""freehire.me job watcher: poll the public API, track new jobs, alert you.

Self-contained subpackage. State lives in SQLite (cache/freehire/jobs.db);
human-facing mirrors and inputs live on the workbook's Freehire sheet.
"""

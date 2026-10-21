"""Digest delivery: one daily email to yourself via Himalaya + a Windows toast.

``digest()`` returns True only when the primary channel delivered. The email is
the primary channel (its failure must not advance any job state, so the next
in-window cycle retries); the Windows notification is best effort.
"""

from __future__ import annotations

import base64
import subprocess

from ..mail.himalaya import Himalaya
from ..send.mime import build_message

# Registered AppUserModelID for PowerShell, so the toast shows a real app name.
_POWERSHELL_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

TIER_ORDER = ("priority", "medium", "low")


class Notifier:
    def __init__(self, cfg, logger, dry_run: bool = False):
        self.cfg = cfg
        self.logger = logger
        self.dry_run = dry_run
        fh = cfg.raw.freehire
        self.email_on = bool(fh.notify_email.raw)
        self.windows_on = bool(fh.notify_windows.raw)
        if not self.email_on and not self.windows_on:
            logger.warning("freehire: all notify channels disabled; digest will just log")

    def digest(self, groups: list[tuple[str, list[dict]]]) -> bool:
        """groups: [(tier_name, [job rows...]), ...] already ordered priority-first."""
        subject, body = render_digest(groups)
        counts_text = _counts_text(groups)
        if self.dry_run:
            print(f"[dry-run] would send email:\nSubject: {subject}\n{body}")
            return True

        primary_ok = True
        if self.email_on:
            primary_ok = self._email(subject, body)
        if self.windows_on:
            if not self._windows("freehire", counts_text):
                self.logger.warning("freehire: windows notification failed (email unaffected)")
            if not self.email_on:
                primary_ok = False
        if not self.email_on and not self.windows_on:
            self.logger.info("freehire digest (channels disabled): %s", subject)
        return primary_ok

    # -- channels ------------------------------------------------------------
    def _email(self, subject: str, body: str) -> bool:
        profile = self.cfg.profile_raw()
        to_email = profile.get("email") or ""
        if not to_email or to_email == "TODO":
            self.logger.error("freehire: no profile email; cannot deliver digest")
            return False
        raw, mid = build_message(
            from_email=to_email, to_email=to_email, subject=subject, body=body,
            from_name=profile.get("full_name") or "",
        )
        hima = Himalaya(self.cfg.raw.himalaya.account.raw, logger=self.logger)
        ok, err = hima.send_raw(raw)
        if ok:
            self.logger.info("freehire digest email sent (%s)", mid)
        else:
            self.logger.error("freehire digest email failed: %s", err)
        return ok

    def _windows(self, title: str, text: str) -> bool:
        return powershell_notify(title, text)


def render_digest(groups: list[tuple[str, list[dict]]]) -> tuple[str, str]:
    counts_text = _counts_text(groups)
    subject = f"[freehire] digest: {counts_text}"
    lines = [counts_text, ""]
    for tier, jobs in groups:
        if not jobs:
            continue
        lines.append(f"{tier.upper()} ({len(jobs)})")
        for i, job in enumerate(jobs, 1):
            slug = job.get("slug") or job.get("public_slug") or ""
            lines.append(f"{i}. {job.get('title', '')} - {job.get('company', '')}")
            location = job.get("location") or ""
            work_mode = job.get("work_mode") or ""
            extra = " | ".join(x for x in (location, work_mode) if x)
            if extra:
                lines.append(f"   {extra}")
            if job.get("url"):
                lines.append(f"   {job['url']}")
            lines.append(f"   python -m outreach freehire applied {slug}")
            lines.append(f"   python -m outreach freehire skip {slug}")
            lines.append(f"   python -m outreach freehire draft {slug}")
            lines.append("")
        lines.append("")
    lines.append("Or set applied = y on the Freehire sheet in outreach.xlsx.")
    return subject, "\n".join(lines) + "\n"


def _counts_text(groups: list[tuple[str, list[dict]]]) -> str:
    total = sum(len(jobs) for _, jobs in groups)
    if not total:
        return "0 new jobs"
    parts = [f"{len(jobs)} {tier}" for tier, jobs in groups if jobs]
    return f"{total} new job(s): " + ", ".join(parts)


def powershell_notify(title: str, text: str) -> bool:
    """Toast with a popup fallback; both run silently from Task Scheduler."""
    t = _ps_quote(title)
    b = _ps_quote(text)
    script = (
        "$ErrorActionPreference='Stop';"
        "try{"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f"$n=$x.GetElementsByTagName('text');$n.Item(0).AppendChild($x.CreateTextNode('{t}'))>$null;"
        f"$n.Item(1).AppendChild($x.CreateTextNode('{b}'))>$null;"
        "$s=[Windows.UI.Notifications.ToastNotification]::new($x);"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_POWERSHELL_AUMID}').Show($s)"
        "}catch{"
        f"(New-Object -ComObject WScript.Shell).Popup('{b}',30,'{t}',64)|Out-Null}}"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ps_quote(value: str) -> str:
    return (value or "").replace("'", "''").replace("`", "").replace("$", "")

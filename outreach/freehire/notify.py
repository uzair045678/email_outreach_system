"""Alert delivery: email to yourself via Himalaya + a Windows notification.

``alert()`` returns True only when the primary channel delivered. The email is
the primary channel (its failure must block state advance so the next cycle
retries); the Windows notification is best effort and only fires on the first
alert for a job.
"""

from __future__ import annotations

import base64
import subprocess

from ..mail.himalaya import Himalaya
from ..send.mime import build_message

# Registered AppUserModelID for PowerShell, so the toast shows a real app name.
_POWERSHELL_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


class Notifier:
    def __init__(self, cfg, logger, dry_run: bool = False):
        self.cfg = cfg
        self.logger = logger
        self.dry_run = dry_run
        fh = cfg.raw.freehire
        self.email_on = bool(fh.notify_email.raw)
        self.windows_on = bool(fh.notify_windows.raw)
        if not self.email_on and not self.windows_on:
            logger.warning("freehire: all notify channels disabled; alerts will just log")

    def alert(self, job: dict, alert_no: int, max_alerts: int) -> bool:
        subject, body = render_alert(job, alert_no, max_alerts)
        if self.dry_run:
            print(f"[dry-run] would send email:\nSubject: {subject}\n{body}")
            return True

        primary_ok = True
        if self.email_on:
            primary_ok = self._email(subject, body)
        if self.windows_on and alert_no == 1:
            if not self._windows(job.get("title") or "freehire", _windows_blurb(job)):
                self.logger.warning("freehire: windows notification failed (email still sent)")
            if not self.email_on:
                primary_ok = False
        if not self.email_on and not self.windows_on:
            self.logger.info("freehire alert (channels disabled): %s", subject)
        return primary_ok

    # -- channels ------------------------------------------------------------
    def _email(self, subject: str, body: str) -> bool:
        profile = self.cfg.profile_raw()
        to_email = profile.get("email") or ""
        if not to_email or to_email == "TODO":
            self.logger.error("freehire: no profile email; cannot deliver alert")
            return False
        raw, mid = build_message(
            from_email=to_email, to_email=to_email, subject=subject, body=body,
            from_name=profile.get("full_name") or "",
        )
        hima = Himalaya(self.cfg.raw.himalaya.account.raw, logger=self.logger)
        ok, err = hima.send_raw(raw)
        if ok:
            self.logger.info("freehire alert email sent (%s)", mid)
        else:
            self.logger.error("freehire alert email failed: %s", err)
        return ok

    def _windows(self, title: str, text: str) -> bool:
        return powershell_notify(title, text)


def render_alert(job: dict, alert_no: int, max_alerts: int) -> tuple[str, str]:
    slug = job.get("public_slug") or job.get("slug") or ""
    if alert_no == 1:
        subject = f"[freehire] NEW: {job.get('title', '')} - {job.get('company', '')}"
        head = "New freehire job match"
    else:
        subject = (f"[freehire] reminder {alert_no}/{max_alerts}: "
                   f"{job.get('title', '')} - {job.get('company', '')}")
        head = f"Reminder {alert_no} of {max_alerts} (still unhandled)"
    body = "\n".join([
        head,
        "",
        f"Title:     {job.get('title', '')}",
        f"Company:   {job.get('company', '')}",
        f"Location:  {job.get('location', '')}",
        f"Work mode: {job.get('work_mode', '')}",
        f"Link:      {job.get('url', '')}",
        f"Job id:    {slug}",
        "",
        f"Mark applied:  python -m outreach freehire applied {slug}",
        f"Skip:          python -m outreach freehire skip {slug}",
        f"Draft email:   python -m outreach freehire draft {slug}",
        "",
        "Or set applied = y on the Freehire sheet in outreach.xlsx.",
    ])
    return subject, body + "\n"


def _windows_blurb(job: dict) -> str:
    return f"{job.get('company', '')} - {job.get('location', '')} - {job.get('work_mode', '')}"


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

import csv
from pathlib import Path

import pytest

from outreach.context import Context
from outreach.store import open_store


class DummyCfg:
    def __init__(self, base: Path):
        self.project_dir = base
        self._paths = {
            "workbook": str(base / "outreach.xlsx"),
            "backup_dir": str(base / "backups"),
            "pending_updates": str(base / "pending_updates.jsonl"),
        }

    def path(self, *keys):
        return Path(self._paths[keys[0]])


class Args:
    def __init__(self, file):
        self.file = file


def test_import_csv_and_dedupe(tmp_path):
    cfg = DummyCfg(tmp_path)
    store = open_store(cfg)

    csv_path = tmp_path / "contacted.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["company", "email", "website"])
        w.writerow(["Alpha Studio", "info@alpha.com", "https://alpha.com"])
        w.writerow(["Beta VR", "", "beta-vr.io"])
        w.writerow(["Alpha Duplicate", "other@alpha.com", "https://www.alpha.com"])
        w.writerow(["", "", ""])

    ctx = Context(cfg=cfg, logger=_SilentLogger(), store=store, args=Args(str(csv_path)))
    from outreach.commands import import_contacted

    rc = import_contacted.run(ctx)
    store.save()

    assert rc == 0
    domains = store.contacted_domains()
    assert "alpha.com" in domains
    assert "beta-vr.io" in domains
    # duplicate registrable domain collapsed to one row
    assert len([d for d in domains if d == "alpha.com"]) == 1
    rows = [r for r in store.wb["Contacted"].iter_rows(min_row=2, values_only=True) if r[0]]
    assert len(rows) == 2


class _SilentLogger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

"""Free address check: MX lookup + one SMTP RCPT TO probe per domain.

Usage:
    python tools/verify_smtp.py info@example.com careers@foo.io ...
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outreach.research.verify import EmailVerifier  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    verifier = EmailVerifier(smtp_probe=True)
    for addr in sys.argv[1:]:
        conf, detail = verifier.verify(addr.strip().lower(), found_on_site=True)
        verdict = {"high": "ACCEPTED", "medium": "UNCERTAIN", "low": "REJECTED"}[conf]
        print(f"{addr:<38} {verdict:<10} [{conf}] {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Import the Gmail Drafts folder into the outreach Queue, ready to schedule.

Each draft becomes a Queue row with a detected recipient country, approve=y and
status=pending_review. Running `python -m outreach send` afterwards turns those
into `approved` rows with a recipient-local send slot (config send_local_hours).

Usage:
    python tools/import_drafts.py            # import new drafts only
    python tools/import_drafts.py --dry-run  # show what would be imported
"""

from __future__ import annotations

import argparse
import email
import email.utils
import json
import re
import subprocess
import sys
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outreach import util
from outreach.config import load_config
from outreach.schedule import tz as tzmod
from outreach.store import open_store

DRAFTS_MAILBOX = "[Gmail]/Drafts"

CCTLD_COUNTRY = {
    "gr": "Greece",
    "sa": "Saudi Arabia",
    "tr": "Turkey",
    "jo": "Jordan",
    "ae": "United Arab Emirates",
    "om": "Oman",
    "qa": "Qatar",
    "kw": "Kuwait",
    "bh": "Bahrain",
    "il": "Israel",
    "lb": "Lebanon",
    "eg": "Egypt",
    "cz": "Czechia",
    "sk": "Slovakia",
    "si": "Slovenia",
    "pl": "Poland",
    "lt": "Lithuania",
    "lv": "Latvia",
    "ee": "Estonia",
    "fi": "Finland",
    "se": "Sweden",
    "no": "Norway",
    "dk": "Denmark",
    "de": "Germany",
    "fr": "France",
    "es": "Spain",
    "it": "Italy",
    "nl": "Netherlands",
    "be": "Belgium",
    "at": "Austria",
    "ch": "Switzerland",
    "pt": "Portugal",
    "ie": "Ireland",
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "ba": "Bosnia and Herzegovina",
    "rs": "Serbia",
    "hr": "Croatia",
    "me": "Montenegro",
    "mk": "North Macedonia",
    "al": "Albania",
    "ro": "Romania",
    "bg": "Bulgaria",
    "hu": "Hungary",
    "ua": "Ukraine",
    "kz": "Kazakhstan",
    "ge": "Georgia",
    "am": "Armenia",
    "az": "Azerbaijan",
    "uz": "Uzbekistan",
    "za": "South Africa",
    "ng": "Nigeria",
    "ke": "Kenya",
    "ma": "Morocco",
    "tn": "Tunisia",
    "pk": "Pakistan",
    "in": "India",
    "my": "Malaysia",
    "sg": "Singapore",
    "id": "Indonesia",
    "th": "Thailand",
    "vn": "Vietnam",
    "ph": "Philippines",
    "cn": "China",
    "jp": "Japan",
    "kr": "South Korea",
    "tw": "Taiwan",
    "hk": "Hong Kong",
    "au": "Australia",
    "nz": "New Zealand",
    "us": "United States",
    "ca": "Canada",
    "mx": "Mexico",
    "br": "Brazil",
    "ar": "Argentina",
    "cl": "Chile",
    "co": "Colombia",
    "pe": "Peru",
    "cr": "Costa Rica",
    "pa": "Panama",
}

COMPOSITE_TLDS = {
    "co.za": "South Africa",
    "com.sa": "Saudi Arabia",
    "com.tr": "Turkey",
    "com.au": "Australia",
    "net.au": "Australia",
    "org.au": "Australia",
    "co.uk": "United Kingdom",
    "com.pk": "Pakistan",
    "com.br": "Brazil",
    "com.mx": "Mexico",
    "co.in": "India",
    "com.sg": "Singapore",
    "com.my": "Malaysia",
    "co.nz": "New Zealand",
    "co.il": "Israel",
    "co.jp": "Japan",
    "co.kr": "South Korea",
    "com.cn": "China",
    "com.tw": "Taiwan",
    "com.hk": "Hong Kong",
    "com.kz": "Kazakhstan",
    "com.ba": "Bosnia and Herzegovina",
    "co.id": "Indonesia",
    "co.th": "Thailand",
    "com.vn": "Vietnam",
    "com.ph": "Philippines",
    "com.ar": "Argentina",
    "com.co": "Colombia",
    "com.pe": "Peru",
    "com.eg": "Egypt",
    "com.ng": "Nigeria",
    "com.ke": "Kenya",
    "com.ma": "Morocco",
}

PLACE_COUNTRY = {
    "dubai": "United Arab Emirates",
    "abu dhabi": "United Arab Emirates",
    "sharjah": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "riyadh": "Saudi Arabia",
    "jeddah": "Saudi Arabia",
    "dammam": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",
    "khobar": "Saudi Arabia",
    "amman": "Jordan",
    "jordan": "Jordan",
    "beirut": "Lebanon",
    "lebanon": "Lebanon",
    "doha": "Qatar",
    "kuwait city": "Kuwait",
    "manama": "Bahrain",
    "muscat": "Oman",
    "cairo": "Egypt",
    "tel aviv": "Israel",
    "jerusalem": "Israel",
    "istanbul": "Turkey",
    "ankara": "Turkey",
    "turkiye": "Turkey",
    "türkiye": "Turkey",
    "athens": "Greece",
    "greece": "Greece",
    "prague": "Czechia",
    "czech": "Czechia",
    "bratislava": "Slovakia",
    "slovakia": "Slovakia",
    "ljubljana": "Slovenia",
    "slovenia": "Slovenia",
    "warsaw": "Poland",
    "krakow": "Poland",
    "poland": "Poland",
    "vilnius": "Lithuania",
    "kaunas": "Lithuania",
    "lithuania": "Lithuania",
    "riga": "Latvia",
    "tallinn": "Estonia",
    "helsinki": "Finland",
    "finland": "Finland",
    "stockholm": "Sweden",
    "oslo": "Norway",
    "copenhagen": "Denmark",
    "berlin": "Germany",
    "munich": "Germany",
    "hamburg": "Germany",
    "germany": "Germany",
    "deutschland": "Germany",
    "paris": "France",
    "france": "France",
    "lyon": "France",
    "madrid": "Spain",
    "barcelona": "Spain",
    "spain": "Spain",
    "rome": "Italy",
    "milan": "Italy",
    "turin": "Italy",
    "italy": "Italy",
    "amsterdam": "Netherlands",
    "rotterdam": "Netherlands",
    "netherlands": "Netherlands",
    "the hague": "Netherlands",
    "brussels": "Belgium",
    "vienna": "Austria",
    "zurich": "Switzerland",
    "geneva": "Switzerland",
    "lisbon": "Portugal",
    "porto": "Portugal",
    "portugal": "Portugal",
    "dublin": "Ireland",
    "london": "United Kingdom",
    "manchester": "United Kingdom",
    "birmingham": "United Kingdom",
    "edinburgh": "United Kingdom",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "sarajevo": "Bosnia and Herzegovina",
    "bosnia": "Bosnia and Herzegovina",
    "belgrade": "Serbia",
    "croatia": "Croatia",
    "zagreb": "Croatia",
    "bucharest": "Romania",
    "romania": "Romania",
    "sofia": "Bulgaria",
    "budapest": "Hungary",
    "kyiv": "Ukraine",
    "almaty": "Kazakhstan",
    "astana": "Kazakhstan",
    "kazakhstan": "Kazakhstan",
    "johannesburg": "South Africa",
    "cape town": "South Africa",
    "durban": "South Africa",
    "south africa": "South Africa",
    "lagos": "Nigeria",
    "nairobi": "Kenya",
    "islamabad": "Pakistan",
    "karachi": "Pakistan",
    "lahore": "Pakistan",
    "pakistan": "Pakistan",
    "mumbai": "India",
    "bangalore": "India",
    "bengaluru": "India",
    "delhi": "India",
    "hyderabad": "India",
    "pune": "India",
    "india": "India",
    "kuala lumpur": "Malaysia",
    "malaysia": "Malaysia",
    "singapore": "Singapore",
    "jakarta": "Indonesia",
    "indonesia": "Indonesia",
    "bangkok": "Thailand",
    "thailand": "Thailand",
    "ho chi minh": "Vietnam",
    "hanoi": "Vietnam",
    "vietnam": "Vietnam",
    "manila": "Philippines",
    "philippines": "Philippines",
    "shanghai": "China",
    "shenzhen": "China",
    "beijing": "China",
    "china": "China",
    "tokyo": "Japan",
    "japan": "Japan",
    "seoul": "South Korea",
    "korea": "South Korea",
    "taipei": "Taiwan",
    "taiwan": "Taiwan",
    "hong kong": "Hong Kong",
    "sydney": "Australia",
    "melbourne": "Australia",
    "brisbane": "Australia",
    "perth": "Australia",
    "australia": "Australia",
    "auckland": "New Zealand",
    "new zealand": "New Zealand",
    "new york": "United States",
    "los angeles": "United States",
    "san francisco": "United States",
    "seattle": "United States",
    "austin": "United States",
    "chicago": "United States",
    "boston": "United States",
    "miami": "United States",
    "denver": "United States",
    "silicon valley": "United States",
    "united states": "United States",
    "usa": "United States",
    "u.s.a": "United States",
    "toronto": "Canada",
    "vancouver": "Canada",
    "montreal": "Canada",
    "canada": "Canada",
    "mexico city": "Mexico",
    "mexico": "Mexico",
    "sao paulo": "Brazil",
    "são paulo": "Brazil",
    "brazil": "Brazil",
    "brasil": "Brazil",
    "buenos aires": "Argentina",
    "argentina": "Argentina",
    "bogota": "Colombia",
    "bogotá": "Colombia",
    "colombia": "Colombia",
    "santiago": "Chile",
    "chile": "Chile",
    "lima": "Peru",
    "peru": "Peru",
}

PHONE_COUNTRY = {
    "+971": "United Arab Emirates",
    "+966": "Saudi Arabia",
    "+962": "Jordan",
    "+961": "Lebanon",
    "+974": "Qatar",
    "+965": "Kuwait",
    "+973": "Bahrain",
    "+968": "Oman",
    "+20": "Egypt",
    "+972": "Israel",
    "+90": "Turkey",
    "+30": "Greece",
    "+420": "Czechia",
    "+421": "Slovakia",
    "+386": "Slovenia",
    "+48": "Poland",
    "+370": "Lithuania",
    "+371": "Latvia",
    "+372": "Estonia",
    "+358": "Finland",
    "+46": "Sweden",
    "+47": "Norway",
    "+45": "Denmark",
    "+49": "Germany",
    "+33": "France",
    "+34": "Spain",
    "+39": "Italy",
    "+31": "Netherlands",
    "+32": "Belgium",
    "+43": "Austria",
    "+41": "Switzerland",
    "+351": "Portugal",
    "+353": "Ireland",
    "+44": "United Kingdom",
    "+387": "Bosnia and Herzegovina",
    "+381": "Serbia",
    "+385": "Croatia",
    "+40": "Romania",
    "+359": "Bulgaria",
    "+36": "Hungary",
    "+380": "Ukraine",
    "+7": "Kazakhstan",
    "+27": "South Africa",
    "+234": "Nigeria",
    "+254": "Kenya",
    "+92": "Pakistan",
    "+91": "India",
    "+60": "Malaysia",
    "+65": "Singapore",
    "+62": "Indonesia",
    "+66": "Thailand",
    "+84": "Vietnam",
    "+63": "Philippines",
    "+86": "China",
    "+81": "Japan",
    "+82": "South Korea",
    "+886": "Taiwan",
    "+852": "Hong Kong",
    "+61": "Australia",
    "+64": "New Zealand",
    "+1": "United States",
    "+52": "Mexico",
    "+55": "Brazil",
    "+54": "Argentina",
    "+56": "Chile",
    "+57": "Colombia",
    "+51": "Peru",
}

LOCAL_HINTS = {
    "lebanon": "Lebanon",
    "dubai": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "ksa": "Saudi Arabia",
}

DOMAIN_COUNTRY = {
    "shaikh-tech.com": "United Arab Emirates",
    "epta-dimensions.com": "Qatar",
    "pikei.io": "Greece",
    "marzooqi.com": "United Arab Emirates",
    "immersiveform.com": "Poland",
    "avar.one": "Estonia",
    "simsectorstudios.com": "North Macedonia",
    "tadreex.com": "Tunisia",
    "stta.eu": "Slovakia",
    "avrlabs.io": "United Arab Emirates",
    "arengineering.tech": "United Arab Emirates",
    "forxaxr.com": "Spain",
    "diginvision.com": "Poland",
    "africxrjob.org": "Tunisia",
    "tajseerksa.com": "Saudi Arabia",
    "gamoola.com": "United Kingdom",
    "infinivirtual.com": "Portugal",
    "miragevirtualreality.com": "Portugal",
    "newlevelxr.com": "Egypt",
    "10ntech.com": "United Arab Emirates",
    "spatiotech.ai": "United Arab Emirates",
    "alpha3d.io": "Estonia",
    "digitaltwin.digital": "South Africa",
    "dcx.studio": "Tunisia",
    "inspirators.eu": "Estonia",
    "artisol.com": "Slovenia",
    "octav.studio": "Morocco",
    "vrsolutions.tech": "Romania",
    "takeleap.com": "United Arab Emirates",
    "mecostech.com": "Turkey",
    "yeppar.com": "India",
    "syskode.com": "Bahrain",
    "heatvr.io": "Ireland",
    "brandxr.io": "United States",
    "xr4industry.com": "Netherlands",
    "redpandainteractive.com": "Finland",
}


def run_himalaya(args: list[str], json_out: bool = False, timeout: int = 120) -> str:
    cmd = ["himalaya"]
    if json_out:
        cmd.append("--json")
    cmd += args
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"himalaya {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def list_drafts() -> list[dict]:
    out = run_himalaya(
        ["envelope", "list", "-m", DRAFTS_MAILBOX, "-s", "100", "-p", "1"], json_out=True
    )
    return json.loads(out or "{}").get("envelopes", [])


def read_draft(message_id: str) -> dict:
    out = run_himalaya(
        ["message", "read", str(message_id), "-m", DRAFTS_MAILBOX, "--raw"], json_out=True
    )
    raw = json.loads(out or "{}").get("message", "")
    msg = email.message_from_string(raw, policy=policy.default)
    to_header = msg.get("To") or ""
    to_email = ""
    for addr in email.utils.getaddresses([to_header]):
        if addr[1]:
            to_email = addr[1].strip().lower()
            break
    subject = str(msg.get("Subject") or "")
    body = ""
    has_attachment = False
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            has_attachment = True
        if ctype == "text/plain" and not body:
            try:
                body = part.get_content()
            except Exception:  # noqa: BLE001
                payload = part.get_payload(decode=True) or b""
                body = payload.decode(part.get_content_charset() or "utf-8", "replace")
    return {
        "to_email": to_email,
        "subject": subject,
        "body": str(body or "").strip(),
        "has_attachment": has_attachment,
        "date": str(msg.get("Date") or ""),
    }


def domain_of(email_addr: str) -> str:
    return email_addr.split("@", 1)[-1].lower().strip(".") if "@" in email_addr else ""


def country_from_tld(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 2:
        composite = ".".join(parts[-2:])
        if composite in COMPOSITE_TLDS:
            return COMPOSITE_TLDS[composite]
        tld = parts[-1]
        if tld in CCTLD_COUNTRY:
            return CCTLD_COUNTRY[tld]
    return ""


def country_from_text(text: str) -> dict[str, int]:
    low = text.lower()
    votes: dict[str, int] = {}
    for needle, country in PLACE_COUNTRY.items():
        if needle in low:
            votes[country] = votes.get(country, 0) + 2
    for needle, country in PHONE_COUNTRY.items():
        if needle in low:
            votes[country] = votes.get(country, 0) + 3
    return votes


def fetch_site_text(fetcher, domain: str) -> str:
    from outreach.research.fetch import html_to_text

    chunks: list[str] = []
    for path in ("/", "/contact", "/contact-us", "/about", "/about-us"):
        for scheme in ("https", "http"):
            html = fetcher.get_html(f"{scheme}://{domain}{path}", timeout=15)
            if html:
                chunks.append(html_to_text(html))
                break
        if len(chunks) >= 2:
            break
    return "\n".join(chunks)


def detect_country(email_addr: str, body: str, html_text: str) -> tuple[str, str, str]:
    domain = domain_of(email_addr)
    override = DOMAIN_COUNTRY.get(domain)
    if override:
        return override, "verified", "high"

    reasons: list[str] = []
    votes: dict[str, int] = {}

    tld_country = country_from_tld(domain)
    if tld_country:
        votes[tld_country] = votes.get(tld_country, 0) + 5
        reasons.append(f"tld:{tld_country}")

    local = email_addr.split("@", 1)[0]
    for hint, country in LOCAL_HINTS.items():
        if hint in local.split("."):
            votes[country] = votes.get(country, 0) + 4
            reasons.append(f"local-part:{country}")

    for source, weight in ((body, 1), (html_text, 2)):
        if not source:
            continue
        for country, count in country_from_text(source).items():
            votes[country] = votes.get(country, 0) + count

    if not votes:
        return "", "", "none"

    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    top_country, top_score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    if top_score >= 5 and top_score > runner:
        confidence = "high"
    elif top_score >= 3 and top_score > runner:
        confidence = "medium"
    else:
        confidence = "low"
    return top_country, "; ".join(reasons) or f"signals:{top_score}", confidence


def company_name(body: str, domain: str) -> str:
    m = re.search(r"(?im)^hi\s+([^,\n]{2,60})[,\s]*$", body or "")
    if m:
        name = re.sub(r"\s*(team|crew|folks|hiring).*$", "", m.group(1), flags=re.I).strip()
        if name:
            return name
    stem = domain.split(".")[0]
    return re.sub(r"(?<!^)(?=[A-Z])", " ", stem).title() if stem.islower() else stem


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Gmail drafts into the outreach Queue.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--limit", type=int, default=None, help="Import at most N drafts.")
    args = parser.parse_args()

    cfg = load_config()
    store = open_store(cfg)
    profile = cfg.profile_raw()
    cv_path = profile.get("cv_pdf_path") or ""
    attach = bool(cfg.raw.toggles.attach_cv.raw)

    from outreach.research.fetch import Fetcher

    fetcher = Fetcher(cfg.path("cache_dir"), ttl_days=30)

    envelopes = list_drafts()
    print(f"found {len(envelopes)} draft(s) in {DRAFTS_MAILBOX}")

    seen_emails = {
        str(r.get("to_email") or "").lower()
        for r in store.queue_all()
        if r.get("to_email")
    }
    imported = 0
    skipped = 0
    rows: list[dict] = []

    for env in envelopes:
        if args.limit is not None and imported >= args.limit:
            break
        draft_id = env.get("id")
        try:
            draft = read_draft(draft_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! draft {draft_id}: {exc}")
            skipped += 1
            continue

        to_email = draft["to_email"]
        if not to_email or not draft["subject"] or not draft["body"]:
            print(f"  ! draft {draft_id}: missing to/subject/body")
            skipped += 1
            continue
        if to_email in seen_emails:
            print(f"  - draft {draft_id}: {to_email} already in queue (duplicate)")
            skipped += 1
            continue

        domain = domain_of(to_email)
        html_text = "" if domain in DOMAIN_COUNTRY else fetch_site_text(fetcher, domain)
        country, reason, confidence = detect_country(to_email, draft["body"], html_text)
        tzname, _guessed = tzmod.recipient_timezone(country) if country else ("UTC", True)
        if not tzmod.country_code(country):
            confidence = "low"

        row = {
            "company": company_name(draft["body"], domain),
            "website": f"https://{domain}" if domain else "",
            "country": country,
            "to_email": to_email,
            "subject": draft["subject"],
            "body": draft["body"],
            "attachment": cv_path if (attach and draft["has_attachment"] and cv_path) else "",
            "template_variant": "initial",
            "status": "pending_review",
            "approve": "y",
            "email_source": f"gmail-draft:{draft_id}",
            "email_confidence": confidence,
            "notes": f"imported from Gmail draft {draft_id} | country={country or '?'} ({reason})",
        }
        rows.append(row)
        seen_emails.add(to_email)
        imported += 1
        print(f"  + draft {draft_id}: {to_email} -> {country or 'UNKNOWN'} [{tzname}] ({confidence})")

    print(f"\n{imported} to import, {skipped} skipped")

    if args.dry_run:
        return 0

    for row in rows:
        store.queue_add(row)
    if not store.save():
        print("WARNING: workbook write failed; updates journaled.")
    print(f"imported {imported} row(s) into {cfg.path('workbook')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

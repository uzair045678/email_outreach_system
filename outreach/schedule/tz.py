"""Recipient timezone resolution from country (+ HQ city when known).

Returns an IANA name plus a ``guessed`` flag. For multi-timezone countries we
use the HQ city if provided, otherwise the most populous zone and mark it as a
guess. Unknown countries fall back to UTC and are always marked guessed.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# country -> (default_tz, iso_code, multi_timezone)
COUNTRY_INFO: dict[str, tuple[str, str, bool]] = {
    # Latin America
    "brazil": ("America/Sao_Paulo", "BR", True),
    "brasil": ("America/Sao_Paulo", "BR", True),
    "mexico": ("America/Mexico_City", "MX", True),
    "argentina": ("America/Argentina/Buenos_Aires", "AR", False),
    "colombia": ("America/Bogota", "CO", False),
    "chile": ("America/Santiago", "CL", True),
    "peru": ("America/Lima", "PE", False),
    "uruguay": ("America/Montevideo", "UY", False),
    "ecuador": ("America/Guayaquil", "EC", False),
    "costa rica": ("America/Costa_Rica", "CR", False),
    "panama": ("America/Panama", "PA", False),
    "guatemala": ("America/Guatemala", "GT", False),
    "dominican republic": ("America/Santo_Domingo", "DO", False),
    "bolivia": ("America/La_Paz", "BO", False),
    "paraguay": ("America/Asuncion", "PY", False),
    # Europe tier 2/3
    "poland": ("Europe/Warsaw", "PL", False),
    "portugal": ("Europe/Lisbon", "PT", True),
    "czech republic": ("Europe/Prague", "CZ", False),
    "czechia": ("Europe/Prague", "CZ", False),
    "romania": ("Europe/Bucharest", "RO", False),
    "greece": ("Europe/Athens", "GR", False),
    "hungary": ("Europe/Budapest", "HU", False),
    "bulgaria": ("Europe/Sofia", "BG", False),
    "croatia": ("Europe/Zagreb", "HR", False),
    "serbia": ("Europe/Belgrade", "RS", False),
    "slovakia": ("Europe/Bratislava", "SK", False),
    "slovenia": ("Europe/Ljubljana", "SI", False),
    "bosnia": ("Europe/Sarajevo", "BA", False),
    "bosnia and herzegovina": ("Europe/Sarajevo", "BA", False),
    "herzegovina": ("Europe/Sarajevo", "BA", False),
    "kazakhstan": ("Asia/Almaty", "KZ", False),
    "pakistan": ("Asia/Karachi", "PK", False),
    "north macedonia": ("Europe/Skopje", "MK", False),
    "macedonia": ("Europe/Skopje", "MK", False),
    "morocco": ("Africa/Casablanca", "MA", False),
    "tunisia": ("Africa/Tunis", "TN", False),
    "estonia": ("Europe/Tallinn", "EE", False),
    "latvia": ("Europe/Riga", "LV", False),
    "lithuania": ("Europe/Vilnius", "LT", False),
    "ukraine": ("Europe/Kyiv", "UA", False),
    "turkey": ("Europe/Istanbul", "TR", False),
    # Middle East
    "uae": ("Asia/Dubai", "AE", False),
    "united arab emirates": ("Asia/Dubai", "AE", False),
    "saudi arabia": ("Asia/Riyadh", "SA", False),
    "israel": ("Asia/Jerusalem", "IL", False),
    "qatar": ("Asia/Qatar", "QA", False),
    "kuwait": ("Asia/Kuwait", "KW", False),
    "bahrain": ("Asia/Bahrain", "BH", False),
    "oman": ("Asia/Muscat", "OM", False),
    "jordan": ("Asia/Amman", "JO", False),
    "lebanon": ("Asia/Beirut", "LB", False),
    "egypt": ("Africa/Cairo", "EG", False),
    "iraq": ("Asia/Baghdad", "IQ", False),
    # Asia
    "china": ("Asia/Shanghai", "CN", False),
    "japan": ("Asia/Tokyo", "JP", False),
    "malaysia": ("Asia/Kuala_Lumpur", "MY", False),
    "singapore": ("Asia/Singapore", "SG", False),
    "indonesia": ("Asia/Jakarta", "ID", True),
    "thailand": ("Asia/Bangkok", "TH", False),
    "vietnam": ("Asia/Ho_Chi_Minh", "VN", False),
    "philippines": ("Asia/Manila", "PH", False),
    "india": ("Asia/Kolkata", "IN", False),
    "south korea": ("Asia/Seoul", "KR", False),
    "korea": ("Asia/Seoul", "KR", False),
    "taiwan": ("Asia/Taipei", "TW", False),
    "hong kong": ("Asia/Hong_Kong", "HK", False),
    # Tier 1 / others
    "united states": ("America/New_York", "US", True),
    "usa": ("America/New_York", "US", True),
    "us": ("America/New_York", "US", True),
    "canada": ("America/Toronto", "CA", True),
    "united kingdom": ("Europe/London", "GB", False),
    "uk": ("Europe/London", "GB", False),
    "germany": ("Europe/Berlin", "DE", False),
    "france": ("Europe/Paris", "FR", False),
    "netherlands": ("Europe/Amsterdam", "NL", False),
    "spain": ("Europe/Madrid", "ES", True),
    "italy": ("Europe/Rome", "IT", False),
    "sweden": ("Europe/Stockholm", "SE", False),
    "norway": ("Europe/Oslo", "NO", False),
    "denmark": ("Europe/Copenhagen", "DK", False),
    "finland": ("Europe/Helsinki", "FI", False),
    "switzerland": ("Europe/Zurich", "CH", False),
    "austria": ("Europe/Vienna", "AT", False),
    "belgium": ("Europe/Brussels", "BE", False),
    "ireland": ("Europe/Dublin", "IE", False),
    "australia": ("Australia/Sydney", "AU", True),
    "new zealand": ("Pacific/Auckland", "NZ", True),
    "russia": ("Europe/Moscow", "RU", True),
    "south africa": ("Africa/Johannesburg", "ZA", False),
    "nigeria": ("Africa/Lagos", "NG", False),
    "kenya": ("Africa/Nairobi", "KE", False),
}

CITY_TZ: dict[str, str] = {
    # US
    "san francisco": "America/Los_Angeles", "los angeles": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "portland": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "san jose": "America/Los_Angeles",
    "austin": "America/Chicago", "dallas": "America/Chicago", "chicago": "America/Chicago",
    "houston": "America/Chicago", "denver": "America/Denver",
    "new york": "America/New_York", "nyc": "America/New_York", "boston": "America/New_York",
    "atlanta": "America/New_York", "miami": "America/New_York", "washington": "America/New_York",
    # Canada
    "toronto": "America/Toronto", "vancouver": "America/Vancouver", "montreal": "America/Toronto",
    # Brazil
    "sao paulo": "America/Sao_Paulo", "são paulo": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo", "brasilia": "America/Sao_Paulo",
    # Mexico
    "mexico city": "America/Mexico_City", "guadalajara": "America/Mexico_City",
    "monterrey": "America/Monterrey", "tijuana": "America/Tijuana",
    # Indonesia
    "jakarta": "Asia/Jakarta", "bandung": "Asia/Jakarta", "surabaya": "Asia/Jakarta",
    "bali": "Asia/Makassar", "denpasar": "Asia/Makassar",
    # China
    "shenzhen": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "guangzhou": "Asia/Shanghai", "hangzhou": "Asia/Shanghai",
    # India
    "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "hyderabad": "Asia/Kolkata",
    # Australia
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane", "perth": "Australia/Perth",
    # Europe
    "london": "Europe/London", "manchester": "Europe/London", "berlin": "Europe/Berlin",
    "munich": "Europe/Berlin", "paris": "Europe/Paris", "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid", "barcelona": "Europe/Madrid", "milan": "Europe/Rome",
    "rome": "Europe/Rome", "warsaw": "Europe/Warsaw", "lisbon": "Europe/Lisbon",
    "istanbul": "Europe/Istanbul", "dubai": "Asia/Dubai", "tel aviv": "Asia/Jerusalem",
}


def _tz_ok(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def recipient_timezone(country: str, city: str = "") -> tuple[str, bool]:
    """Return (iana_tz, guessed)."""
    c = (country or "").strip().lower()
    city_key = (city or "").strip().lower()

    if city_key and city_key in CITY_TZ and _tz_ok(CITY_TZ[city_key]):
        return CITY_TZ[city_key], False

    info = COUNTRY_INFO.get(c)
    if info is None:
        # try to match a country contained in the string
        for key, value in COUNTRY_INFO.items():
            if key in c and c:
                info = value
                break
    if info is None:
        return "UTC", True

    tz, _iso, multi = info
    if not _tz_ok(tz):
        return "UTC", True
    return tz, multi


def country_code(country: str) -> str:
    c = (country or "").strip().lower()
    info = COUNTRY_INFO.get(c)
    if info is None:
        for key, value in COUNTRY_INFO.items():
            if key in c and c:
                info = value
                break
    return info[1] if info else ""

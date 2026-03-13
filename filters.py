import html
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Age filter — skip jobs older than this
# ---------------------------------------------------------------------------

MAX_AGE_DAYS = 45

# ---------------------------------------------------------------------------
# Keyword filters — job title must match at least one
# ---------------------------------------------------------------------------

INCLUDE_KEYWORDS = [
    "marketing",
    "growth marketer",
    "growth manager",
    "growth lead",
    "growth director",
    "community",
    "content",
    "brand",
    "gtm",
    "go-to-market",
    "partnerships",
    "kol",
    "social media",
    "communications",
    " pr ",
    "public relations",
    "customer acquisition",
    "user acquisition",
    "ambassador",
    "influencer",
    "awareness",
    "campaign",
    "narrative",
    "ecosystem",
    "demand generation",
    "product marketing",
    "growth marketing",
]

# These in the TITLE → not actually a marketing role
EXCLUDE_TITLE_PHRASES = [
    "talent acquisition",   # HR/recruiting
    "frontend engineer",
    "backend engineer",
    "software engineer",
    "engineering manager",
    "engineering director",
    "data engineer",
    "principal engineer",
    "algorithm engineer",
    "business intelligence",
    "customer care",
    "customer success",
    "customer support",
    "game reviewer",
    "content delivery",     # operational/logistics, not marketing
    "content moderator",
    "content moderation",
    "human resources",
    "hr lead",
    "hr manager",
    "recruiting",
    "recruiter",
    "legal counsel",
    "compliance",
    "risk manager",
    "financial analyst",
    "data analyst",
    "data scientist",
    "machine learning",
    "qa engineer",
    "qa lead",
    "security engineer",
    "security analyst",
    "network engineer",
    "site reliability",
    "devops",
    "devrel",
    "developer relations",
    "developer advocate",
    "developer advocacy",
    "developer evangelist",
]

# ---------------------------------------------------------------------------
# Location filter — open remote only
# A job passes ONLY if it has a remote/global keyword AND no geo qualifier.
# ---------------------------------------------------------------------------

# These keywords signal truly open remote work (must have at least one)
REMOTE_KEYWORDS = [
    "remote",
    "worldwide",
    "global",
    "anywhere",
    "distributed",
]

# Geo qualifiers — if ANY of these appear in the location string, the job
# is geo-restricted (even if "remote" is also present) and gets blocked.
GEO_QUALIFIERS = [
    # Regions
    "europe", "european", "emea", "apac", "latam", "latin america",
    "asia", "asia pacific", "africa", "middle east",
    "north america", "south america", "oceania",
    # Countries
    "united states", "united kingdom", "great britain",
    "usa", "us ", " us,", "(us)", "(usa)",
    " uk", "uk ", "(uk)",
    "germany", "france", "spain", "italy", "netherlands", "poland",
    "portugal", "sweden", "norway", "denmark", "finland", "belgium",
    "austria", "switzerland", "ireland", "greece", "czech", "hungary",
    "canada", "australia", "new zealand", "india", "japan", "china",
    "south korea", "korea", "brazil", "argentina", "mexico", "colombia",
    "turkey", "nigeria", "south africa", "kenya", "egypt",
    "singapore", "hong kong", "dubai", "uae",
    # Major cities (when they appear in location, job is tied to that city)
    "new york", "san francisco", "los angeles", "chicago", "seattle",
    "boston", "austin", "miami", "nyc", "bay area",
    "london", "berlin", "paris", "amsterdam", "madrid", "rome",
    "toronto", "vancouver", "sydney", "melbourne",
    "mumbai", "bangalore", "delhi", "tokyo", "shanghai", "beijing",
    # Generic geo-restrict phrases
    "must be based in", "must reside in", "must be located in",
    "must live in", "residency required", "work authorization",
    " only", # catches "europe only", "uk only" but not "remote only"
]

# On-site patterns — always excluded
ONSITE_PATTERNS = [
    "on-site",
    "onsite",
    "in-office",
    "hybrid",
]


# Companies known to geo-restrict all roles (even when listed as "Remote")
COMPANY_DENYLIST = [
    "tether",
    "tether operations",
]

# Geo phrases in job TITLES that indicate a geo-restricted role
TITLE_GEO_PHRASES = [
    " - us",
    " (us)",
    ", us",
    " - usa",
    "americas",
    " latam",
    " apac",
    " emea",
    " europe",
    " uk",
    " irl ",   # "IRL" = in real life = on-site
    "(irl)",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(text: str) -> str:
    return html.unescape(text or "")


def _parse_posted_date(posted: str) -> Optional[datetime]:
    """Try every reasonable date format and return a UTC-aware datetime or None."""
    if not posted:
        return None
    posted = str(posted).strip()
    if not posted or posted in ("None", "0", ""):
        return None

    # Lever: Unix timestamp in milliseconds (13 digits)
    if re.fullmatch(r"\d{13}", posted):
        try:
            return datetime.fromtimestamp(int(posted) / 1000, tz=timezone.utc)
        except Exception:
            pass

    # Unix timestamp in seconds (10 digits)
    if re.fullmatch(r"\d{10}", posted):
        try:
            return datetime.fromtimestamp(int(posted), tz=timezone.utc)
        except Exception:
            pass

    # ISO 8601 fast path
    iso_candidate = posted
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass

    # Common explicit datetime formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(posted, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            pass

    # RFC 2822 (RSS/Atom: "Thu, 19 Feb 2026 06:32:03 GMT")
    try:
        dt = parsedate_to_datetime(posted)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass

    return None


def _is_too_old(job) -> bool:
    """Return True if the job was posted more than MAX_AGE_DAYS ago."""
    dt = _parse_posted_date(job.posted)
    if dt is None:
        return False   # unknown date → assume recent, don't discard
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    return dt < cutoff


def _matches_include(title: str) -> bool:
    t = _decode(title).lower()
    return any(kw in t for kw in INCLUDE_KEYWORDS)


def _is_excluded_title(title: str) -> bool:
    t = _decode(title).lower()
    for phrase in EXCLUDE_TITLE_PHRASES:
        if phrase in t:
            if phrase == "product manager" and ("product marketing" in t or "growth marketing" in t):
                continue
            return True
    return False


def _is_location_allowed(job) -> bool:
    """
    Open-remote filter:
    - Empty location → allow (assume globally open)
    - On-site patterns → deny
    - Must contain a remote/global keyword → deny if absent
    - Even with "remote", deny if a geo qualifier is also present
      (means geo-restricted remote, e.g. "Remote - Mexico", "Remote LATAM")
    """
    loc = _decode(job.location or "").lower().strip()
    if not loc:
        return True

    # Always block on-site
    if any(p in loc for p in ONSITE_PATTERNS):
        return False

    # Must have at least one open-remote keyword
    if not any(kw in loc for kw in REMOTE_KEYWORDS):
        return False

    # Block even if remote when a geo qualifier is present
    if any(q in loc for q in GEO_QUALIFIERS):
        return False

    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_filters(jobs: list) -> list:
    """
    Keep jobs that:
    1. Title matches a marketing/growth keyword
    2. Title doesn't match an exclude phrase
    3. Location is remote, worldwide, or Dubai/Singapore/HK (or unknown)
    4. Were posted within the last MAX_AGE_DAYS days
    """
    result = []
    for job in jobs:
        if not _matches_include(job.title):
            continue
        if _is_excluded_title(job.title):
            continue
        if not _is_location_allowed(job):
            continue
        if _is_too_old(job):
            continue
        # Block denylisted companies (geo-restrict all roles regardless of listing)
        company_lower = _decode(job.company or "").lower()
        if any(d in company_lower for d in COMPANY_DENYLIST):
            continue
        # Block titles with geo-restriction phrases
        title_lower = _decode(job.title or "").lower()
        if any(p in title_lower for p in TITLE_GEO_PHRASES):
            continue
        result.append(job)
    return result

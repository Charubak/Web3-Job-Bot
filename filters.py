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
    "devrel",
    "developer relations",
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
]

# ---------------------------------------------------------------------------
# Location allowlist — ONLY these pass
# Everything else with a specific geography is excluded
# ---------------------------------------------------------------------------

ALLOWED_LOCATION_KEYWORDS = [
    "remote",
    "worldwide",
    "global",
    "anywhere",
    "distributed",
    "dubai",
    "uae",
    "singapore",
    "hong kong",
]

# US-restricted patterns — also excluded even if they contain "remote"
US_RESTRICTED_PATTERNS = [
    "us only",
    "us citizen",
    "must be in us",
    "us work authorization",
    "remote - usa",
    "remote, usa",
    "remote - us",
    "remote, us",
    "us / remote",
    "remote (us)",
    "remote (usa)",
    "remote (united states)",
    "united states",
    # US cities
    "new york",
    "san francisco",
    "austin",
    "los angeles",
    "boston",
    "chicago",
    "seattle",
    "miami",
    "denver",
    "nyc",
    "bay area",
    "silicon valley",
    "remote - ny",
    "remote - ca",
    "california",
    "texas",
    "washington, d",
]

# On-site patterns — excluded everywhere
ONSITE_PATTERNS = [
    "on-site",
    "onsite",
    "in-office",
    "hybrid",
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

    # ISO 8601 variants
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            s = posted[: len(fmt) + 6]   # +6 for timezone suffix
            dt = datetime.strptime(posted[:19], fmt[:19])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

    # RFC 2822 (RSS/Atom: "Thu, 19 Feb 2026 06:32:03 GMT")
    try:
        return parsedate_to_datetime(posted)
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
    Allowlist approach:
    - Empty location → allow (assume remote / unknown)
    - Contains an allowed keyword → allow (subject to US check below)
    - Anything else → deny
    """
    loc = _decode(job.location or "").lower().strip()
    if not loc:
        return True

    # US-restricted and on-site always deny
    if any(p in loc for p in US_RESTRICTED_PATTERNS):
        return False
    if any(p in loc for p in ONSITE_PATTERNS):
        return False

    # Must contain at least one allowed keyword
    return any(kw in loc for kw in ALLOWED_LOCATION_KEYWORDS)


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
        result.append(job)
    return result

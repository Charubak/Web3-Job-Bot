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
# Role presets — built-in keyword lists for common Web3 job categories
# Each entry is a list of strings that must appear in the job title (lowercase).
# ---------------------------------------------------------------------------

ROLE_PRESETS = {
    "marketing": [
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
        "campaign",
        "narrative",
        "ecosystem",
        "devrel",
        "developer relations",
        "demand generation",
        "product marketing",
        "growth marketing",
    ],
    "engineering": [
        "engineer",
        "developer",
        "solidity",
        "smart contract",
        "blockchain developer",
        "backend",
        "frontend",
        "full stack",
        "fullstack",
        "rust developer",
        "typescript developer",
        "web3 developer",
        "protocol engineer",
        "infrastructure engineer",
        "dapp",
        "evm",
        "zkp",
        "zero knowledge",
        "cryptography engineer",
    ],
    "legal": [
        "legal",
        "compliance",
        "counsel",
        "regulatory",
        "policy",
        "general counsel",
        "legal officer",
        "chief legal",
    ],
    "design": [
        "designer",
        " design",
        "ux ",
        "ui/ux",
        "ui ",
        "product design",
        "visual design",
        "graphic designer",
        "creative director",
        "motion designer",
    ],
    "product": [
        "product manager",
        "product lead",
        "head of product",
        "product owner",
        "chief product",
        "vp product",
        "product director",
    ],
    "operations": [
        "operations manager",
        "ops manager",
        "chief of staff",
        "finance manager",
        "people operations",
        "head of operations",
        "treasury",
        "financial controller",
    ],
    "bd": [
        "business development",
        "bd manager",
        "bd lead",
        "head of bd",
        "account executive",
        "account manager",
        "revenue",
        "partner manager",
        "head of sales",
        "sales manager",
        "partnerships manager",
    ],
    "research": [
        "researcher",
        "research analyst",
        "protocol researcher",
        "security researcher",
        "economist",
        "quantitative researcher",
        "cryptographer",
        "defi researcher",
    ],
    "data": [
        "data analyst",
        "data scientist",
        "data engineer",
        "analytics engineer",
        "business intelligence",
        "data lead",
        "head of data",
        "quantitative analyst",
    ],
}

# Per-role exclusion phrases (applied only when that role is selected)
ROLE_EXCLUDE_PHRASES = {
    "marketing": [
        "frontend engineer",
        "backend engineer",
        "software engineer",
        "engineering manager",
        "engineering director",
        "data engineer",
        "principal engineer",
        "algorithm engineer",
        "content delivery",
        "content moderator",
        "content moderation",
        "human resources",
        "hr lead",
        "hr manager",
        "recruiting",
        "recruiter",
        "legal counsel",
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
    ],
    "engineering": [
        "marketing manager",
        "content manager",
        "community manager",
        "brand manager",
        "social media manager",
        "recruiting",
        "recruiter",
    ],
    "legal": [
        "marketing",
        "engineering",
        "design",
        "recruiter",
    ],
    "design": [
        "marketing manager",
        "engineering",
        "recruiter",
    ],
    "product": [
        "marketing",
        "recruiter",
    ],
    "operations": [
        "recruiter",
        "talent acquisition",
    ],
    "bd": [
        "marketing manager",
        "engineering",
        "recruiter",
    ],
    "research": [
        "recruiter",
        "marketing manager",
    ],
    "data": [
        "recruiter",
        "marketing manager",
        "community manager",
    ],
}

# ---------------------------------------------------------------------------
# Build keyword lists from config
# ---------------------------------------------------------------------------

def _build_include_keywords() -> list:
    """Build include keyword list from JOB_ROLES config setting."""
    try:
        from config import JOB_ROLES
    except Exception:
        JOB_ROLES = ["marketing"]

    keywords = []
    for role in JOB_ROLES:
        if role in ROLE_PRESETS:
            keywords.extend(ROLE_PRESETS[role])
        else:
            keywords.append(role)   # custom keyword

    # Deduplicate, preserve order
    seen, result = set(), []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def _build_exclude_phrases() -> list:
    """Build exclusion phrase list from selected roles."""
    try:
        from config import JOB_ROLES
    except Exception:
        JOB_ROLES = ["marketing"]

    phrases = []
    for role in JOB_ROLES:
        phrases.extend(ROLE_EXCLUDE_PHRASES.get(role, []))
    seen, result = set(), []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


INCLUDE_KEYWORDS   = _build_include_keywords()
EXCLUDE_TITLE_PHRASES = _build_exclude_phrases()

# ---------------------------------------------------------------------------
# Location allowlist (used when LOCATION_TYPE=remote)
# ---------------------------------------------------------------------------

_REMOTE_KEYWORDS = [
    "remote",
    "worldwide",
    "global",
    "anywhere",
    "distributed",
]

# US-restricted patterns — excluded even when they mention "remote"
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

# On-site patterns — excluded everywhere (for remote and specific modes)
ONSITE_PATTERNS = [
    "on-site",
    "onsite",
    "in-office",
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
    dt = _parse_posted_date(job.posted)
    if dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    return dt < cutoff


def _matches_include(title: str) -> bool:
    t = _decode(title).lower()
    return any(kw in t for kw in INCLUDE_KEYWORDS)


def _is_excluded_title(title: str) -> bool:
    t = _decode(title).lower()
    for phrase in EXCLUDE_TITLE_PHRASES:
        if phrase in t:
            # Don't exclude "product marketing" or "growth marketing" for marketing role
            if phrase == "product manager" and ("product marketing" in t or "growth marketing" in t):
                continue
            return True
    return False


def _is_location_allowed(job) -> bool:
    """
    Filter based on LOCATION_TYPE from config:
      any      → always allow
      remote   → allowlist (remote/worldwide/global) + block US-restricted
      specific → allow if matches PREFERRED_LOCATIONS or is remote
    """
    try:
        from config import LOCATION_TYPE, PREFERRED_LOCATIONS
    except Exception:
        LOCATION_TYPE = "remote"
        PREFERRED_LOCATIONS = []

    if LOCATION_TYPE == "any":
        return True

    loc = _decode(job.location or "").lower().strip()
    if not loc:
        return True   # unknown → assume remote

    # On-site always denied in remote and specific modes
    if any(p in loc for p in ONSITE_PATTERNS):
        return False

    if LOCATION_TYPE == "remote":
        if any(p in loc for p in US_RESTRICTED_PATTERNS):
            return False
        return any(kw in loc for kw in _REMOTE_KEYWORDS)

    if LOCATION_TYPE == "specific":
        # Allow remote-sounding jobs
        if any(kw in loc for kw in _REMOTE_KEYWORDS):
            # Still reject US-restricted remote
            if any(p in loc for p in US_RESTRICTED_PATTERNS):
                return False
            return True
        # Allow preferred locations
        return any(city in loc for city in PREFERRED_LOCATIONS)

    return False   # unknown LOCATION_TYPE → deny


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_filters(jobs: list) -> list:
    """
    Keep jobs that:
    1. Title matches at least one configured role keyword
    2. Title doesn't match a role-specific exclusion phrase
    3. Location passes the configured location mode
    4. Posted within the last MAX_AGE_DAYS days
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

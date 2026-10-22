"""
Job board adapters. Each adapter returns a list of Job dataclasses.
Failures in individual boards are caught and logged so others still run.
"""

import hashlib
import html
import re
import time
from copy import copy
from threading import BoundedSemaphore
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobScraper/1.0)"}

# Keep peak parsing/response memory small even on a 256 MiB machine. ATS
# companies are fetched sequentially inside their board, without nested pools.
MAX_BOARD_WORKERS = 2
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_SECONDS = 30
_HTTP_SLOTS = BoundedSemaphore(MAX_BOARD_WORKERS)


def _get(url: str, **kwargs) -> httpx.Response:
    """Fetch a bounded, decoded body; RSS must use this too, never URL parsing."""
    kwargs.setdefault("headers", HEADERS)
    kwargs["headers"] = {"Accept-Encoding": "identity", **kwargs["headers"]}
    kwargs.setdefault("timeout", 15)
    with _HTTP_SLOTS:
        started = time.monotonic()
        with httpx.Client(max_redirects=5) as client:
            with client.stream("GET", url, **kwargs) as response:
                # A decoded-byte cap also protects against compressed bodies.
                body = bytearray()
                size = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > MAX_REQUEST_SECONDS:
                        raise TimeoutError(f"Response exceeded {MAX_REQUEST_SECONDS}s: {url}")
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError(f"Response exceeded {MAX_RESPONSE_BYTES} bytes: {url}")
                    body.extend(chunk)
                if time.monotonic() - started > MAX_REQUEST_SECONDS:
                    raise TimeoutError(f"Response exceeded {MAX_REQUEST_SECONDS}s: {url}")
                # iter_bytes has already decoded Content-Encoding. Do not decode
                # a second time when constructing the buffered response.
                headers = {k: v for k, v in response.headers.items()
                           if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
                return httpx.Response(response.status_code, headers=headers,
                                      content=bytes(body), request=response.request)


def _plain_text(value: str) -> str:
    return BeautifulSoup(html.unescape(value or ""), "html.parser").get_text(" ", strip=True)


def _fetch_feed(url: str):
    response = _get(url, follow_redirects=True, timeout=15)
    response.raise_for_status()
    return feedparser.parse(response.content)


def _rss_description(entry) -> str:
    parts = [entry.get("summary", "")]
    parts.extend(item.get("value", "") for item in entry.get("content", []))
    return "\n".join(dict.fromkeys(_plain_text(part) for part in parts if part))


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    source: str
    salary: str = ""
    posted: str = ""
    description: str = ""
    employer_country: str = ""
    employer_country_source: str = ""
    employer_country_verified_at: str = ""
    # Proven existence by this date (e.g. ATS last-publication), never original date.
    published_at_upper_bound: str = ""
    # Set only by a live, trusted source that lists this requisition as open.
    current_open_verified: bool = False


_TRACKING_QUERY_KEYS = {
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
    "gh_src", "lever-source", "lever-origin",
}


def canonical_job_url(url: str) -> str:
    """Remove safe tracking variations without erasing a requisition's identity.

    Unknown query keys (including ref/source/job IDs), their ordering and raw
    encoding remain intact. Hash routes and job-identifying fragments may name
    different jobs, so only ordinary page anchors are discarded. This also
    preserves existing hashes for URLs that are already canonical.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return url.strip()
    query = []
    for item in parts.query.split("&"):
        key = unquote_plus(item.partition("=")[0]).lower()
        if key.startswith("utm_") or key in _TRACKING_QUERY_KEYS:
            continue
        query.append(item)
    fragment = parts.fragment
    if not ("/" in fragment or "=" in fragment or
            re.search(r"(?:job|posting|position|vacancy|requisition)[-_:]\w", fragment, re.I)):
        fragment = ""
    # Never alter potential userinfo; ordinary public posting hosts are safe
    # to lowercase because DNS names are case-insensitive.
    netloc = parts.netloc if "@" in parts.netloc else parts.netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, "&".join(query), fragment))


def _make_id(url: str) -> str:
    return hashlib.sha256(canonical_job_url(url).encode()).hexdigest()[:16]


def _company_text(value: str) -> str:
    """A shifted table column containing an age is not employer evidence."""
    value = html.unescape(value).strip()
    if re.fullmatch(r"(?:today|yesterday|just now|\d+\s*(?:[smhdwy]|seconds?|minutes?|hours?|days?|weeks?|months?|years?)(?:\s+ago)?)", value, re.I):
        return ""
    return value


_URL_SHORTENERS = ("tinyurl.com", "bit.ly", "ow.ly", "t.co", "buff.ly", "rb.gy", "cutt.ly")

def _resolve_url(url: str) -> str:
    """Follow redirects for shortened URLs. Returns empty string if final URL is 404."""
    if not any(s in url for s in _URL_SHORTENERS):
        return url
    try:
        with _HTTP_SLOTS:
            with httpx.Client(max_redirects=5) as client:
                resp = client.head(url, follow_redirects=True, timeout=8, headers=HEADERS)
        final = str(resp.url)
        if not final.startswith("http"):
            return url
        # Drop the job if the resolved page is gone
        if resp.status_code == 404:
            return ""
        return final
    except Exception:
        return url


def _tg_posted(msg) -> str:
    """Extract ISO datetime from a Telegram message's <time> element."""
    time_el = msg.select_one("time[datetime]")
    return time_el["datetime"] if time_el else ""


def _split_title_company(raw: str) -> tuple[str, str]:
    """Split 'Job Title at Company Name' into (title, company)."""
    raw = html.unescape(raw)
    # Use the last ' at ' occurrence so companies like 'Marketing at Scale at Acme' parse right
    parts = raw.rsplit(" at ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return raw.strip(), ""


def _extract_location_from_summary(summary: str) -> str:
    """Preserve explicit location fields; marketing language is not eligibility.

    A company describing its global/distributed team says nothing about the
    countries where it can hire. Unstructured evidence stays in description.
    """
    soup = BeautifulSoup(html.unescape(summary or ""), "html.parser")
    # Keep inline tags on the same line so "Remote <b>US only</b>" cannot
    # accidentally become an unqualified Remote field.
    for tag in soup.find_all(["br", "p", "div", "li"]):
        tag.insert_after("\n")
    text = soup.get_text(" ", strip=False)
    match = re.search(r"(?:^|\n)\s*(?:job\s+)?location\s*:\s*([^\n]+)", text, re.I)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------------------
# Board 1: cryptocurrencyjobs.co  (RSS — all jobs, filter by title later)
# ---------------------------------------------------------------------------

def fetch_cryptocurrencyjobs() -> list[Job]:
    try:
        feed = _fetch_feed("https://cryptocurrencyjobs.co/index.xml")
        jobs = []
        for entry in feed.entries:
            url = entry.get("link", "")
            if not url:
                continue
            raw_title = entry.get("title", "").strip()
            title, company = _split_title_company(raw_title)
            summary = entry.get("summary", "")
            location = _extract_location_from_summary(summary)
            description = _rss_description(entry)
            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="cryptocurrencyjobs.co",
                    posted=entry.get("published", ""),
                    description=description,
                )
            )
        print(f"[cryptocurrencyjobs.co] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[cryptocurrencyjobs.co] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 2: web3.career  (HTTP scraping — category pages)
# ---------------------------------------------------------------------------

def _scrape_web3career_page(path: str) -> list[Job]:
    base = "https://web3.career"
    url = base + path
    resp = _get(url, headers=HEADERS, follow_redirects=True, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []
    for row in soup.select("tr[onclick]"):
        # URL from onclick attribute: onclick="tableTurboRowClick(event, '/slug/id')"
        onclick = row.get("onclick", "")
        match = re.search(r"'(/[^']+)'", onclick)
        if not match:
            continue
        job_path = match.group(1)
        job_url = base + job_path

        tds = row.find_all("td")
        if len(tds) < 2:
            continue

        # Both old and current layouts use semantic headings. In the current
        # layout title/company/location share the first td; td[1] is the age.
        h2 = row.find("h2")
        title = h2.get_text(strip=True) if h2 else tds[0].get_text(strip=True)

        h3 = row.find("h3")
        company = _company_text(h3.get_text(strip=True) if h3 else
                                tds[1].get_text(strip=True) if not tds[1].find("time") else "")

        # Keep the whole explicit location, including inline qualifiers such
        # as "Remote - US only" that do not necessarily have their own link.
        locations = []
        for location_el in row.select(".job-location-mobile"):
            value = location_el.get_text(" ", strip=True).replace("📍", "").strip()
            value = re.sub(r"\s+,", ",", value)
            if value:
                locations.append(value)
        for loc_link in row.find_all("a", href=lambda h: h and "/web3-jobs-" in h):
            if not loc_link.find_parent(class_="job-location-mobile"):
                locations.append(loc_link.get_text(" ", strip=True))
        location = "; ".join(dict.fromkeys(locations))

        # Prefer an absolute posting timestamp over a rounded display age.
        time_el = row.find("time")
        posted = (time_el.get("datetime") or time_el.get_text(strip=True)) if time_el else ""

        if not title:
            continue

        jobs.append(
            Job(
                id=_make_id(job_url),
                title=title,
                company=company,
                location=location,
                url=job_url,
                source="web3.career",
                posted=posted,
                description=row.get_text(" ", strip=True),
            )
        )
    return jobs


def fetch_web3career() -> list[Job]:
    paths = [
        "/marketing-jobs",
        "/community-manager-jobs",
        "/content-jobs",
        "/growth-jobs",
    ]
    all_jobs: list[Job] = []
    seen_ids: set[str] = set()
    for path in paths:
        try:
            jobs = _scrape_web3career_page(path)
            for j in jobs:
                if j.id not in seen_ids:
                    all_jobs.append(j)
                    seen_ids.add(j.id)
        except Exception as e:
            from source_health import record_failure
            record_failure()
            print(f"[web3.career] ERROR ({path}): {e}")
    print(f"[web3.career] {len(all_jobs)} jobs fetched")
    return all_jobs


# ---------------------------------------------------------------------------
# Board 3: cryptojobslist.com  (HTTP scraping — table rows)
# ---------------------------------------------------------------------------

def fetch_cryptojobslist() -> list[Job]:
    base = "https://cryptojobslist.com"
    try:
        resp = _get(
            f"{base}/marketing",
            headers=HEADERS,
            follow_redirects=True,
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        rows = soup.select("tr")
        for row in rows[1:]:  # skip header row
            tds = row.find_all("td")
            if len(tds) < 5:
                continue

            # Title + link from first td
            a_tag = tds[0].find("a", href=lambda h: h and "/jobs/" in str(h))
            if not a_tag:
                continue
            href = a_tag["href"]
            job_url = href if href.startswith("http") else base + href
            title = html.unescape(tds[0].get_text(strip=True))

            company = html.unescape(tds[1].get_text(strip=True))
            # TD[3] is salary (often empty), TD[4] is location
            salary = tds[3].get_text(strip=True) if len(tds) > 3 else ""
            location = tds[4].get_text(strip=True) if len(tds) > 4 else ""
            posted = tds[6].get_text(strip=True) if len(tds) > 6 else ""

            if not title:
                continue

            jobs.append(
                Job(
                    id=_make_id(job_url),
                    title=title,
                    company=company,
                    location=location,
                    url=job_url,
                    source="cryptojobslist.com",
                    salary=salary,
                    posted=posted,
                    description=row.get_text(" ", strip=True),
                )
            )
        print(f"[cryptojobslist.com] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[cryptojobslist.com] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 4: remote3.co  (RSS — all jobs, filter by title later)
# ---------------------------------------------------------------------------

def fetch_remote3() -> list[Job]:
    try:
        # Fetch bytes ourselves so redirects, timeouts and size limits apply.
        feed = _fetch_feed("https://www.remote3.co/api/rss")
        jobs = []
        for entry in feed.entries:
            url = entry.get("link", "")
            if not url:
                continue

            raw_title = entry.get("title", "").strip()
            title, company = _split_title_company(raw_title)

            # Keep the actual location slot, including country-only values.
            summary = _plain_text(entry.get("summary", ""))
            parts = [p.strip() for p in summary.split(" - ")]
            location = parts[2] if len(parts) >= 3 else _extract_location_from_summary(summary)
            salary = ""
            if len(parts) > 3:
                # "Remote - US" must not be reduced to just "Remote".
                tail = []
                for part in parts[3:]:
                    if "$" in part or "/yr" in part or "/mo" in part:
                        salary = part
                    else:
                        tail.append(part)
                if tail:
                    location = " - ".join([location] + tail)

            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="remote3.co",
                    salary=salary,
                    posted=entry.get("published", ""),
                    description=_rss_description(entry),
                )
            )
        print(f"[remote3.co] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[remote3.co] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 5: Wellfound — skipped (requires JS / returns 403 without auth)
# Replaced with: cryptojobslist.com /web3 page (more tags coverage)
# ---------------------------------------------------------------------------

def fetch_cryptojobslist_web3() -> list[Job]:
    """Additional cryptojobslist scrape targeting broader web3 tag page."""
    base = "https://cryptojobslist.com"
    try:
        resp = _get(
            f"{base}/web3",
            headers=HEADERS,
            follow_redirects=True,
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        rows = soup.select("tr")
        for row in rows[1:]:
            tds = row.find_all("td")
            if len(tds) < 5:
                continue
            a_tag = tds[0].find("a", href=lambda h: h and "/jobs/" in str(h))
            if not a_tag:
                continue
            href = a_tag["href"]
            job_url = href if href.startswith("http") else base + href
            title = tds[0].get_text(strip=True)
            company = tds[1].get_text(strip=True)
            salary = tds[3].get_text(strip=True) if len(tds) > 3 else ""
            location = tds[4].get_text(strip=True) if len(tds) > 4 else ""
            posted = tds[6].get_text(strip=True) if len(tds) > 6 else ""
            if not title:
                continue
            jobs.append(
                Job(
                    id=_make_id(job_url),
                    title=title,
                    company=company,
                    location=location,
                    url=job_url,
                    source="cryptojobslist.com",
                    salary=salary,
                    posted=posted,
                    description=row.get_text(" ", strip=True),
                )
            )
        print(f"[cryptojobslist.com/web3] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[cryptojobslist.com/web3] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 6: Greenhouse ATS — public JSON API, no auth required
# Covers: Coinbase, Consensys, Alchemy, Ripple, Fireblocks, BitGo, Gemini,
#         Nansen, Avalabs, Paradigm, Messari, Figment, Solana Foundation
# ---------------------------------------------------------------------------

GREENHOUSE_COMPANIES = [
    ("coinbase",        "Coinbase"),
    ("consensys",       "Consensys"),
    ("alchemy",         "Alchemy"),
    ("ripple",          "Ripple"),
    ("fireblocks",      "Fireblocks"),
    ("bitgo",           "BitGo"),
    ("gemini",          "Gemini"),
    ("nansen",          "Nansen"),
    ("avalabs",         "Ava Labs"),
    ("paradigm",        "Paradigm"),
    ("messari",         "Messari"),
    ("figment",         "Figment"),
    ("solana",          "Solana Foundation"),
    ("okx",             "OKX"),
    ("openzeppelin",    "OpenZeppelin"),
    ("jumpcrypto",      "Jump Crypto"),
]


def _fetch_greenhouse_one(slug: str, company_name: str) -> list[Job]:
    try:
        resp = _get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            params={"content": "true"},
            headers=HEADERS,
            timeout=12,
        )
        if resp.status_code != 200:
            from source_health import record_failure
            record_failure()
            print(f"[greenhouse/{slug}] HTTP {resp.status_code}; source unavailable")
            return []
        jobs = []
        for j in resp.json().get("jobs", []):
            url = j.get("absolute_url", "")
            if not url:
                continue
            title = html.unescape(j.get("title", "").strip())
            location = j.get("location", {}).get("name", "")
            # updated_at can refresh an old requisition; it is not its age.
            posted = j.get("first_published") or ""
            jobs.append(Job(
                id=_make_id(url), title=title, company=company_name,
                location=location, url=url, source=f"greenhouse/{slug}", posted=posted,
                description=_plain_text(j.get("content", "")), current_open_verified=True,
            ))
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[greenhouse/{slug}] ERROR: {e}")
        return []


def fetch_greenhouse() -> list[Job]:
    """Query Greenhouse companies without creating a nested thread pool."""
    all_jobs: list[Job] = []
    for slug, name in GREENHOUSE_COMPANIES:
        all_jobs.extend(_fetch_greenhouse_one(slug, name))
    print(f"[greenhouse] {len(all_jobs)} jobs fetched across {len(GREENHOUSE_COMPANIES)} companies")
    return all_jobs


# ---------------------------------------------------------------------------
# Board 7: Lever ATS — public JSON API, no auth required
# Covers: Binance, 1inch, CertiK, Anchorage, Ledger
# ---------------------------------------------------------------------------

LEVER_COMPANIES = [
    ("binance",        "Binance"),
    ("1inch",          "1inch"),
    ("certik",         "CertiK"),
    ("anchorage",      "Anchorage Digital"),
    ("ledger",         "Ledger"),
    ("immutable",      "Immutable"),
    ("animocabrands",  "Animoca Brands"),
    ("aurora-dev",     "Aurora (NEAR)"),
    ("offchainlabs",   "Arbitrum (Offchain Labs)"),
    # Verified official board: https://jobs.lever.co/jito (2026-10-19).
    ("jito",           "Jito Foundation"),
]


def _lever_location(posting: dict) -> str:
    categories = posting.get("categories") or {}
    locations = [categories.get("location", "")]
    locations.extend(categories.get("allLocations") or [])
    workplace = posting.get("workplaceType", "")
    if workplace in {"remote", "hybrid", "on-site"}:
        locations.append(workplace)
    return "; ".join(dict.fromkeys(str(value).strip() for value in locations if value))


def _lever_description(posting: dict) -> str:
    parts = [posting.get("openingPlain") or posting.get("opening", ""),
             posting.get("descriptionPlain") or posting.get("description", "")]
    for section in posting.get("lists") or []:
        parts.extend([section.get("text", ""), section.get("content", "")])
    parts.append(posting.get("additionalPlain") or posting.get("additional", ""))
    return "\n".join(_plain_text(part) for part in parts if part)


def _fetch_lever_one(slug: str, company_name: str) -> list[Job]:
    try:
        resp = _get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            headers=HEADERS,
            timeout=12,
        )
        if resp.status_code != 200:
            from source_health import record_failure
            record_failure()
            print(f"[lever/{slug}] HTTP {resp.status_code}; source unavailable")
            return []
        jobs = []
        for j in resp.json():
            url = j.get("hostedUrl", "")
            if not url:
                continue
            title = html.unescape(j.get("text", "").strip())
            location = _lever_location(j)
            posted = str(j.get("createdAt") or "")
            description = _lever_description(j)
            jobs.append(Job(
                id=_make_id(url), title=title, company=company_name,
                location=location, url=url, source=f"lever/{slug}", posted=posted,
                description=description, current_open_verified=True,
            ))
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[lever/{slug}] ERROR: {e}")
        return []


def fetch_lever() -> list[Job]:
    """Query Lever companies without creating a nested thread pool."""
    all_jobs: list[Job] = []
    for slug, name in LEVER_COMPANIES:
        all_jobs.extend(_fetch_lever_one(slug, name))
    print(f"[lever] {len(all_jobs)} jobs fetched across {len(LEVER_COMPANIES)} companies")
    return all_jobs


# ---------------------------------------------------------------------------
# Board 8: blockace.io — HTML scraping, all remote web3 jobs
# ---------------------------------------------------------------------------

def fetch_blockace() -> list[Job]:
    """Scrape blockace.io job listings."""
    base = "https://blockace.io"
    try:
        resp = _get(base, headers=HEADERS, follow_redirects=True, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        for card in soup.select("a[href*='/jobs/']"):
            url = card.get("href", "")
            if not url.startswith("http"):
                url = base + url

            title_el = card.select_one("h3")
            title = html.unescape(title_el.get_text(strip=True)) if title_el else ""

            company_el = card.select_one("p[class*='Company']")
            company = html.unescape(company_el.get_text(strip=True)) if company_el else ""

            loc_el = card.select_one("p[class*='Location']")
            location = loc_el.get_text(strip=True) if loc_el else ""

            salary_parts = [
                p.get_text(strip=True)
                for p in card.select("p[class*='Meta']")
                if "$" in p.get_text() or any(c.isdigit() for c in p.get_text())
            ]
            salary = " - ".join(salary_parts) if salary_parts else ""

            if not title:
                continue

            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="blockace.io",
                    salary=salary,
                    description=card.get_text(" ", strip=True),
                )
            )
        print(f"[blockace.io] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[blockace.io] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Board 9: RemoteOK — public JSON API, no auth, crypto+web3 filtered
# ---------------------------------------------------------------------------

def fetch_remoteok() -> list[Job]:
    """RemoteOK public JSON endpoint for crypto+web3 jobs."""
    try:
        resp = _get(
            "https://remoteok.com/remote-crypto+web3-jobs.json",
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        jobs = []
        for j in resp.json():
            if not isinstance(j, dict) or not j.get("position"):
                continue
            url = j.get("apply_url") or j.get("url", "")
            if not url:
                continue
            title = html.unescape(j.get("position", "").strip())
            company = html.unescape(j.get("company", "").strip())
            location = j.get("location", "") or ""
            s_min = j.get("salary_min", 0) or 0
            s_max = j.get("salary_max", 0) or 0
            salary = f"${s_min:,}–${s_max:,}" if s_min and s_max else ""
            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="remoteok.com",
                    salary=salary,
                    posted=j.get("date", ""),
                    description=_plain_text(j.get("description", "")),
                )
            )
        print(f"[remoteok.com] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[remoteok.com] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 10: crypto.jobs — HTML scraping
# ---------------------------------------------------------------------------

def fetch_cryptojobs() -> list[Job]:
    """Scrape crypto.jobs job listing page."""
    base = "https://crypto.jobs"
    try:
        resp = _get(
            f"{base}/jobs",
            headers=HEADERS,
            follow_redirects=True,
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        for row in soup.select("tr.job-entry"):
            card = row.select_one("a.job-url")
            if not card:
                continue
            href = card.get("href", "")
            url = href if href.startswith("http") else base + href

            title_el = card.select_one("p.job-title")
            title = html.unescape(title_el.get_text(strip=True)) if title_el else ""

            spans = card.find_all("span", recursive=False)
            company = html.unescape(spans[0].get_text(strip=True)) if spans else ""
            if not company:
                for s in card.find_all("span"):
                    t = s.get_text(strip=True)
                    if t and len(t) < 80:
                        company = t
                        break

            location = ""
            small = card.select_one("div.hidden-xs small")
            if small:
                for span in small.find_all("span"):
                    t = span.get_text(strip=True)
                    if "🌍" in t or "remote" in t.lower():
                        location = t.replace("🌍", "").strip()
                        break

            # Date: "16 days ago" text in a <small> inside the row (outside the card)
            posted = ""
            for small_el in row.select("small"):
                txt = small_el.get_text(strip=True)
                if re.search(r"\d+\s*(day|hour|week|month)", txt, re.I):
                    posted = txt
                    break

            if not title:
                continue

            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="crypto.jobs",
                    posted=posted,
                    description=row.get_text(" ", strip=True),
                )
            )
        print(f"[crypto.jobs] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[crypto.jobs] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 11: @web3hiring Telegram channel — 58k subscribers, active daily
# Catches companies not in our ATS lists (Magic Eden, Circle, etc.)
# Scrapes the public t.me/s/ web preview (no Telegram API needed)
# ---------------------------------------------------------------------------

def fetch_web3hiring_telegram() -> list[Job]:
    """Scrape the @web3hiring public Telegram channel preview."""
    try:
        resp = _get(
            "https://t.me/s/web3hiring",
            headers=HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for msg in soup.select(".tgme_widget_message_wrap"):
            text_el = msg.select_one(".tgme_widget_message_text")
            if not text_el:
                continue

            # Extract external links (skip t.me links)
            ext_links = [
                a["href"] for a in msg.select("a[href]")
                if "http" in a.get("href", "") and "t.me" not in a["href"]
            ]
            if not ext_links:
                continue

            url = _resolve_url(ext_links[0])
            if not url:
                continue
            full_text = text_el.get_text("\n", strip=True)
            lines = [l.strip() for l in full_text.splitlines() if l.strip()]

            # Format: "Company is hiring\nRole Name" or "Company\nRole"
            company, title = "", ""
            if len(lines) >= 2:
                company_line = lines[0]
                company = re.sub(r"\s+is\s+hiring$", "", company_line, flags=re.IGNORECASE).strip()
                title = lines[1]
            elif lines:
                title = lines[0]

            if not title or len(title) < 4:
                continue

            jobs.append(
                Job(
                    id=_make_id(url),
                    title=html.unescape(title),
                    company=html.unescape(company),
                    location="",  # unverified; never infer global eligibility
                    url=url,
                    source="@web3hiring",
                    posted=_tg_posted(msg),
                    description=full_text,
                )
            )

        print(f"[@web3hiring] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[@web3hiring] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 12: Lever — Aave Labs (EU endpoint)
# ---------------------------------------------------------------------------

def fetch_lever_aave() -> list[Job]:
    """Aave Labs uses the EU Lever endpoint."""
    try:
        resp = _get(
            "https://api.eu.lever.co/v0/postings/aavelabs?mode=json",
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            from source_health import record_failure
            record_failure()
            print(f"[lever/aave] {resp.status_code}")
            return []
        jobs = []
        for j in resp.json():
            url = j.get("hostedUrl", "")
            if not url:
                continue
            title = html.unescape(j.get("text", "").strip())
            location = _lever_location(j)
            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company="Aave Labs",
                    location=location,
                    url=url,
                    source="lever/aave",
                    posted=str(j.get("createdAt") or ""),
                    description=_lever_description(j), current_open_verified=True,
                )
            )
        print(f"[lever/aave] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[lever/aave] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 13: @cryptojobsdaily Telegram — structured "Company/Title/Location" posts
# Posts are formatted: Company: X \n Title: Y \n Location: Z \n Apply Here: URL
# ---------------------------------------------------------------------------

def fetch_cryptojobsdaily_telegram() -> list[Job]:
    """Scrape the @cryptojobsdaily public Telegram channel (structured job posts)."""
    try:
        resp = _get(
            "https://t.me/s/cryptojobsdaily",
            headers=HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for msg in soup.select(".tgme_widget_message_wrap"):
            text_el = msg.select_one(".tgme_widget_message_text")
            if not text_el:
                continue

            full_text = text_el.get_text("\n", strip=True)
            lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]

            # Parse structured lines: "Company: X", "Title: Y", "Location: Z"
            fields: dict[str, str] = {}
            for line in lines:
                for key in ("company", "title", "location"):
                    prefix = key + ":"
                    if line.lower().startswith(prefix):
                        fields[key] = line[len(prefix):].strip()

            if "title" not in fields:
                continue

            # URL from external links in message
            ext_links = [
                a["href"] for a in msg.select("a[href]")
                if "http" in a.get("href", "") and "t.me" not in a["href"]
            ]
            url = _resolve_url(ext_links[0]) if ext_links else ""
            if not url:
                continue

            jobs.append(
                Job(
                    id=_make_id(url),
                    title=html.unescape(fields.get("title", "")),
                    company=html.unescape(fields.get("company", "")),
                    location=fields.get("location", ""),
                    url=url,
                    source="@cryptojobsdaily",
                    posted=_tg_posted(msg),
                    description=full_text,
                )
            )

        print(f"[@cryptojobsdaily] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[@cryptojobsdaily] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 14: @cryptojobslist Telegram — emoji-delimited structured posts
# Posts: 💼 title  🏛️ at company  🌍 location  💰 salary  ✅ Apply → URL
# ---------------------------------------------------------------------------

def fetch_cryptojobslist_telegram() -> list[Job]:
    """Scrape the @cryptojobslist public Telegram channel (emoji-structured posts)."""
    try:
        resp = _get(
            "https://t.me/s/cryptojobslist",
            headers=HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for msg in soup.select(".tgme_widget_message_wrap"):
            text_el = msg.select_one(".tgme_widget_message_text")
            if not text_el:
                continue

            full_text = text_el.get_text("\n", strip=True)
            lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]

            # Each emoji is on its own line; content follows on the NEXT line
            title = company = location = salary = ""
            EMOJI_MARKERS = {
                "\U0001f4bc": "title",      # 💼
                "\U0001f3db": "company",    # 🏛️
                "\U0001f30d": "location",   # 🌍
                "\U0001f4b0": "salary",     # 💰
            }
            pending_field = None
            for line in lines:
                stripped = line.strip().rstrip("\ufe0f")  # strip variation selector
                # Check if this line is a lone emoji marker
                if stripped in EMOJI_MARKERS:
                    pending_field = EMOJI_MARKERS[stripped]
                    continue
                # If we're waiting for content after an emoji, capture it
                if pending_field:
                    value = line.strip()
                    if pending_field == "company":
                        value = re.sub(r"^at\b[\s:,-]*", "", value, flags=re.IGNORECASE).strip()
                    if pending_field == "title":
                        title = value
                    elif pending_field == "company":
                        company = value
                    elif pending_field == "location":
                        location = value
                    elif pending_field == "salary":
                        salary = value
                    pending_field = None

            if not title:
                continue

            ext_links = [
                a["href"] for a in msg.select("a[href]")
                if "http" in a.get("href", "") and "t.me" not in a["href"]
            ]
            url = _resolve_url(ext_links[0]) if ext_links else ""
            if not url:
                continue

            jobs.append(
                Job(
                    id=_make_id(url),
                    title=html.unescape(title),
                    company=html.unescape(company),
                    location=location,
                    url=url,
                    source="@cryptojobslist",
                    salary=salary,
                    posted=_tg_posted(msg),
                    description=full_text,
                )
            )

        print(f"[@cryptojobslist] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[@cryptojobslist] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Board 15: Remotive — public JSON API, no auth, remote jobs only
# Broad remote job board with crypto/blockchain/web3 categories
# ---------------------------------------------------------------------------

def fetch_remotive() -> list[Job]:
    """Remotive public API — Marketing category, filtered for web3/crypto titles."""
    try:
        resp = _get(
            "https://remotive.com/api/remote-jobs",
            params={"category": "Marketing"},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            from source_health import record_failure
            record_failure()
            print(f"[remotive.com] HTTP {resp.status_code}")
            return []
        web3_kw = ["crypto", "blockchain", "web3", "defi", "nft", "token", "dao", "dex", "protocol", "layer"]
        jobs = []
        for j in resp.json().get("jobs", []):
            title_co = (j.get("title", "") + " " + j.get("company_name", "") + " " + j.get("description", "")[:200]).lower()
            if not any(kw in title_co for kw in web3_kw):
                continue
            url = j.get("url", "")
            if not url:
                continue
            title = html.unescape(j.get("title", "").strip())
            company = html.unescape(j.get("company_name", "").strip())
            location = j.get("candidate_required_location", "") or ""
            posted = j.get("publication_date", "")
            salary = j.get("salary", "") or ""
            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="remotive.com",
                    salary=salary,
                    posted=posted,
                    description=_plain_text(j.get("description", "")),
                )
            )
        print(f"[remotive.com] {len(jobs)} web3 marketing jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[remotive.com] ERROR: {e}")
        return []




# X.com hiring leads via a public search index, not complete X/Twitter access.
# Index coverage and freshness are best effort; zero results are possible.
# A post/repost/index timestamp is not the original job posting date. These
# leads stay unverified until a first-party job listing supplies the evidence.
# The /twitter bot command provides manual search links as fallback.
# ---------------------------------------------------------------------------

_BING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TWITTER_QUERIES = [
    '(site:x.com OR site:twitter.com) (web3 OR crypto) hiring (growth OR GTM)',
    '(site:x.com OR site:twitter.com) (web3 OR crypto) hiring ("product marketing" OR PMM)',
    '(site:x.com OR site:twitter.com) crypto hiring ("head of growth" OR "growth lead")',
    '(site:x.com OR site:twitter.com) web3 hiring senior "go-to-market"',
]
_TWITTER_MAX_QUERIES = 4
_TWITTER_RESULTS_PER_QUERY = 20


def _twitter_post_url(raw_url: str) -> str:
    """Validate an exact public post host/path; decode Bing links locally only.

    Search result links can use Bing's encoded click wrapper. Only that exact
    wrapper is decoded, once, without following redirects or visiting X.
    """
    import base64
    from urllib.parse import parse_qs

    if not isinstance(raw_url, str) or len(raw_url) > 4096:
        return ""
    url = html.unescape(raw_url).strip()
    if re.search(r"[\x00-\x20\x7f\\]", url):
        return ""
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or parts.username is not None
                or parts.password is not None or parts.port not in {None, 80, 443}):
            return ""
        if parts.hostname in {"bing.com", "www.bing.com"} and parts.path == "/ck/a":
            values = parse_qs(parts.query, max_num_fields=100).get("u", [])
            if len(values) != 1 or not values[0].startswith("a1"):
                return ""
            encoded = values[0][2:]
            url = base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                                   altchars=b"-_", validate=True).decode("utf-8")
            if re.search(r"[\x00-\x20\x7f\\]", url):
                return ""
            parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"}
                or parts.hostname not in {"x.com", "twitter.com"}
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443}):
            return ""
        match = re.fullmatch(
            r"/([A-Za-z0-9_]{1,15}|i/web)/status/([0-9]+)"
            r"(?:/(?:photo|video)/[1-9][0-9]*)?/?", parts.path,
        )
        if not match:
            return ""
        # X and legacy Twitter URLs identify the same post. Tracking parameters
        # and media suffixes cannot turn it into another discovery candidate.
        return f"https://x.com/{match.group(1).lower()}/status/{match.group(2)}"
    except (ValueError, UnicodeError):
        return ""


def fetch_twitter_bing() -> list[Job]:
    """Bounded free discovery for Growth/GTM/PMM hiring, not verified jobs.

    Request a past-month index window so the newest jobs through 30 days can be discovered.
    Search freshness is only a hint and never supplies the strict original-job
    age check. No login, paid API, X requests, pagination, or challenge bypass.
    """
    jobs = []
    seen_urls: set[str] = set()

    for query in _TWITTER_QUERIES[:_TWITTER_MAX_QUERIES]:
        try:
            resp = _get(
                "https://www.bing.com/search",
                params={"q": query, "freshness": "Month", "filters": 'ex1:"ez3"',
                        "count": _TWITTER_RESULTS_PER_QUERY},
                headers=_BING_HEADERS,
                timeout=15,
                follow_redirects=True,
            )
            if resp.status_code in {403, 429}:
                from source_health import record_failure
                record_failure()
                print(f"[twitter/bing] Search unavailable ({resp.status_code}); stopping discovery")
                break
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if soup.select_one("#b_captcha, .b_captcha, form[action*='captcha'], iframe[src*='captcha']"):
                from source_health import record_failure
                record_failure()
                print("[twitter/bing] Search challenge; stopping discovery")
                break

            for result in soup.select("li.b_algo")[:_TWITTER_RESULTS_PER_QUERY]:
                link = result.select_one("h2 a")
                if not link:
                    continue
                url = _twitter_post_url(link.get("href", ""))
                if not url or url in seen_urls:
                    continue

                # Bing h2 for tweets: 'Handle on X: "tweet text..."'
                h2_text = link.get_text(" ", strip=True)
                tweet_match = re.search(r' on X: [\u201c"](.*)', h2_text, re.DOTALL)
                if tweet_match:
                    title = tweet_match.group(1).rstrip('\u201d"').strip()
                else:
                    snippet_el = result.select_one(".b_caption p")
                    title = snippet_el.get_text(" ", strip=True) if snippet_el else h2_text
                title = title[:200]  # cap length
                if not title:
                    continue
                seen_urls.add(url)

                jobs.append(
                    Job(
                        id=_make_id(url),
                        title=title,
                        # The poster may be a recruiter or a reposting account;
                        # neither its handle nor the snippet proves an employer.
                        company="",
                        location="",
                        url=url,
                        source="x.com",
                        posted="",
                        description=result.get_text(" ", strip=True)[:8000],
                    )
                )
        except Exception as e:
            from source_health import record_failure
            record_failure()
            print(f"[twitter/bing] ERROR ({query[:40]}): {e}")

    print(f"[twitter/bing] {len(jobs)} unverified indexed posts found (best-effort coverage)")
    return jobs


# ---------------------------------------------------------------------------
# Board 16: @web3marketingjobs Telegram — exclusively web3 marketing roles
# Format: "🚨 Title @ Company\nLearn more: URL\nCompany description..."
# ---------------------------------------------------------------------------

def fetch_web3marketingjobs_telegram() -> list[Job]:
    """Scrape @web3marketingjobs — 100% web3 marketing job posts."""
    try:
        resp = _get(
            "https://t.me/s/web3marketingjobs",
            headers=HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for msg in soup.select(".tgme_widget_message_wrap"):
            text_el = msg.select_one(".tgme_widget_message_text")
            if not text_el:
                continue
            lines = [l.strip() for l in text_el.get_text("\n", strip=True).splitlines() if l.strip()]

            # Format: line 0 = "🚨", line 1 = "Title @ Company"
            title = company = ""
            for line in lines[:3]:
                m = re.match(r"(.+?)\s+@\s+(.+)", line)
                if m and len(m.group(1)) > 3:
                    title = html.unescape(m.group(1).strip())
                    company = html.unescape(m.group(2).strip())
                    break
            if not title:
                continue

            ext_links = [
                a["href"] for a in msg.select("a[href]")
                if "http" in a.get("href", "") and "t.me" not in a["href"]
                and "/job/" in a.get("href", "")  # only specific job pages, not homepage
            ]
            url = ext_links[0] if ext_links else ""
            if not url:
                continue

            jobs.append(Job(
                id=_make_id(url),
                title=title,
                company=company,
                location="",
                url=url,
                source="@web3marketingjobs",
                posted=_tg_posted(msg),
                description=text_el.get_text("\n", strip=True),
            ))

        print(f"[@web3marketingjobs] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[@web3marketingjobs] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 17: @web3_marketing_jobs Telegram — web3 marketing roles
# Format: "📢 Company is Hiring! Position: Title Location: Loc Apply now: URL"
# ---------------------------------------------------------------------------

def fetch_web3_marketing_jobs_telegram() -> list[Job]:
    """Scrape @web3_marketing_jobs — web3 marketing job posts."""
    try:
        resp = _get(
            "https://t.me/s/web3_marketing_jobs",
            headers=HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for msg in soup.select(".tgme_widget_message_wrap"):
            text_el = msg.select_one(".tgme_widget_message_text")
            if not text_el:
                continue
            full_text = text_el.get_text(" ", strip=True)

            # Format: "📢 Company is Hiring! Position: Title Location: Loc"
            company_m = re.search(r"📢\s+(.+?)\s+is Hiring!", full_text)
            title_m = re.search(r"Position:\s+(.+?)(?:\s+Location:|$)", full_text)
            loc_m = re.search(r"Location:\s+(.+?)(?:\s+Apply|$)", full_text)
            if not company_m or not title_m:
                continue

            title = html.unescape(title_m.group(1).strip())
            company = html.unescape(company_m.group(1).strip())
            location = loc_m.group(1).strip() if loc_m else ""
            # Strip flag emojis from location
            location = re.sub(r"[\U0001F1E0-\U0001F1FF]", "", location).strip()

            ext_links = [
                a["href"] for a in msg.select("a[href]")
                if "http" in a.get("href", "") and "t.me" not in a["href"]
            ]
            url = ext_links[0] if ext_links else ""
            if not url:
                continue

            jobs.append(Job(
                id=_make_id(url),
                title=title,
                company=company,
                location=location,
                url=url,
                source="@web3_marketing_jobs",
                posted=_tg_posted(msg),
                description=full_text,
            ))

        print(f"[@web3_marketing_jobs] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        from source_health import record_failure
        record_failure()
        print(f"[@web3_marketing_jobs] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_hylo_verified() -> list[Job]:
    from hylo_source import fetch_hylo
    return fetch_hylo()


def fetch_linkedin_hiring_posts() -> list[Job]:
    from linkedin_discovery import fetch_linkedin_posts
    return fetch_linkedin_posts()


def fetch_curated_companies() -> list[Job]:
    from company_roles import fetch_company_roles
    return fetch_company_roles()


def fetch_direct_ashby() -> list[Job]:
    from direct_sources import fetch_ashby
    return fetch_ashby()


def fetch_direct_gains() -> list[Job]:
    from direct_sources import fetch_gains
    return fetch_gains()


def fetch_direct_hacken() -> list[Job]:
    from direct_sources import fetch_hacken
    return fetch_hacken()


BOARDS = [
    fetch_cryptocurrencyjobs,
    fetch_web3career,
    fetch_cryptojobslist,
    fetch_remote3,
    fetch_cryptojobslist_web3,
    fetch_greenhouse,
    fetch_lever,
    fetch_blockace,
    fetch_remoteok,
    fetch_cryptojobs,
    fetch_web3hiring_telegram,
    fetch_lever_aave,
    fetch_cryptojobsdaily_telegram,
    fetch_cryptojobslist_telegram,
    fetch_web3marketingjobs_telegram,
    fetch_web3_marketing_jobs_telegram,
    fetch_remotive,
    fetch_twitter_bing,
    fetch_direct_ashby,
    fetch_direct_gains,
    fetch_direct_hacken,
    fetch_hylo_verified,
    fetch_curated_companies,
    fetch_linkedin_hiring_posts,
]


def _title_company_key(job: Job) -> str:
    """Normalised key to catch the same job listed on multiple boards."""
    t = html.unescape(job.title or "").lower().strip()
    c = html.unescape(job.company or "").lower().strip()
    # strip common suffixes that vary between boards, e.g. "(remote)", "(global - remote)"
    import re as _re
    t = _re.sub(r"\s*\(.*?\)\s*$", "", t).strip()
    return f"{t}||{c}"


def _preserve_duplicate_posting_dates(jobs: list[Job], now: datetime) -> list[Job]:
    """Keep oldest same-requisition evidence before duplicate rows disappear.

    A title/company match is deliberately insufficient here. Primary posting
    dates outrank aggregator publication dates for the identical canonical URL.
    Missing/future dates remain uncertain unless an older copy proves expiry.
    """
    from filters import MAX_AGE_DAYS, _is_primary_source, _parse_posted_date

    primary_urls = {canonical_job_url(job.url) for job in jobs
                    if job.url and _is_primary_source(job)}
    earliest = {}
    upper_bounds = {}
    for job in jobs:
        if not job.url:
            continue
        key = canonical_job_url(job.url)
        if key in primary_urls and not _is_primary_source(job):
            continue
        posted = _parse_posted_date(job.posted, now=now)
        if posted is not None and posted <= now:
            earliest[key] = min(earliest.get(key, posted), posted)
        upper = _parse_posted_date(getattr(job, "published_at_upper_bound", ""), now=now)
        if upper is not None and upper <= now:
            upper_bounds[key] = min(upper_bounds.get(key, upper), upper)

    reconciled = []
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    for job in jobs:
        oldest = earliest.get(canonical_job_url(job.url)) if job.url else None
        posted = _parse_posted_date(job.posted, now=now)
        if oldest is not None and ((posted is not None and oldest < posted <= now) or
                                   ((posted is None or posted > now) and oldest < cutoff)):
            job = copy(job)
            job.posted = oldest.isoformat()
        upper = upper_bounds.get(canonical_job_url(job.url)) if job.url else None
        if upper is not None:
            job = copy(job)
            job.published_at_upper_bound = upper.isoformat()
        reconciled.append(job)
    return reconciled


def fetch_all(*, now: datetime | None = None) -> list[Job]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from filters import _is_primary_source

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    results: dict[int, list[Job]] = {}

    from source_health import reset, run_source
    reset()
    with ThreadPoolExecutor(max_workers=MAX_BOARD_WORKERS) as executor:
        futures = {executor.submit(run_source, fn): i for i, fn in enumerate(BOARDS)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                from source_health import record_failure
                record_failure()
                print(f"[fetch_all] Board {BOARDS[idx].__name__} ERROR: {e}")
                results[idx] = []

    all_jobs: list[Job] = []
    seen_ids: set[str] = set()
    seen_title_company: set[str] = set()

    candidates = _preserve_duplicate_posting_dates(
        [job for idx in range(len(BOARDS)) for job in results.get(idx, [])], now)
    candidates.sort(key=lambda job: (
        not _is_primary_source(job),
        not bool(job.description),
        not bool(job.location),
    ))
    for job in candidates:
        tc_key = _title_company_key(job)
        # Unknown companies cannot safely identify cross-board duplicates.
        is_primary = _is_primary_source(job)
        # Separate ATS requisitions can have the same title but different
        # eligibility. Keep both; suppress lower-information reposts instead.
        if job.id in seen_ids or (not is_primary and job.company.strip() and tc_key in seen_title_company):
            continue
        all_jobs.append(job)
        seen_ids.add(job.id)
        if job.company.strip():
            seen_title_company.add(tc_key)

    return all_jobs


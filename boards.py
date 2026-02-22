"""
Job board adapters. Each adapter returns a list of Job dataclasses.
Failures in individual boards are caught and logged so others still run.
"""

import hashlib
import html
import re
from dataclasses import dataclass

import feedparser
import httpx
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobScraper/1.0)"}


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


def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _split_title_company(raw: str) -> tuple[str, str]:
    """Split 'Job Title at Company Name' into (title, company)."""
    raw = html.unescape(raw)
    # Use the last ' at ' occurrence so companies like 'Marketing at Scale at Acme' parse right
    parts = raw.rsplit(" at ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return raw.strip(), ""


def _extract_location_from_summary(summary: str) -> str:
    """Pull location hints out of an RSS summary string."""
    text = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True).lower()
    for keyword in ["worldwide", "global", "anywhere", "remote", "on-site", "hybrid"]:
        if keyword in text:
            # Return title-cased version
            return keyword.title()
    return ""


# ---------------------------------------------------------------------------
# Board 1: cryptocurrencyjobs.co  (RSS — all jobs, filter by title later)
# ---------------------------------------------------------------------------

def fetch_cryptocurrencyjobs() -> list[Job]:
    try:
        feed = feedparser.parse("https://cryptocurrencyjobs.co/index.xml")
        jobs = []
        for entry in feed.entries:
            url = entry.get("link", "")
            if not url:
                continue
            raw_title = entry.get("title", "").strip()
            title, company = _split_title_company(raw_title)
            location = _extract_location_from_summary(entry.get("summary", ""))
            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    source="cryptocurrencyjobs.co",
                    posted=entry.get("published", ""),
                )
            )
        print(f"[cryptocurrencyjobs.co] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[cryptocurrencyjobs.co] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 2: web3.career  (HTTP scraping — category pages)
# ---------------------------------------------------------------------------

def _scrape_web3career_page(path: str) -> list[Job]:
    base = "https://web3.career"
    url = base + path
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
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

        # Title: h2 inside first td
        h2 = tds[0].find("h2")
        title = h2.get_text(strip=True) if h2 else tds[0].get_text(strip=True)

        # Company: h3 inside second td
        h3 = tds[1].find("h3")
        company = h3.get_text(strip=True) if h3 else tds[1].get_text(strip=True)

        # Location: td containing a link to /web3-jobs-<location>
        location = ""
        for td in tds[2:]:
            loc_link = td.find("a", href=lambda h: h and "/web3-jobs-" in h)
            if loc_link:
                location = loc_link.get_text(strip=True)
                break

        # Posted: time element
        time_el = row.find("time")
        posted = time_el.get_text(strip=True) if time_el else ""

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
            print(f"[web3.career] ERROR ({path}): {e}")
    print(f"[web3.career] {len(all_jobs)} jobs fetched")
    return all_jobs


# ---------------------------------------------------------------------------
# Board 3: cryptojobslist.com  (HTTP scraping — table rows)
# ---------------------------------------------------------------------------

def fetch_cryptojobslist() -> list[Job]:
    base = "https://cryptojobslist.com"
    try:
        resp = httpx.get(
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
                )
            )
        print(f"[cryptojobslist.com] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[cryptojobslist.com] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 4: remote3.co  (RSS — all jobs, filter by title later)
# ---------------------------------------------------------------------------

def fetch_remote3() -> list[Job]:
    try:
        # Must use www subdomain — bare domain redirects to www which feedparser doesn't follow
        feed = feedparser.parse("https://www.remote3.co/api/rss")
        jobs = []
        for entry in feed.entries:
            url = entry.get("link", "")
            if not url:
                continue

            raw_title = entry.get("title", "").strip()
            title, company = _split_title_company(raw_title)

            # Summary format: "at Company - Type - Location - $salary"
            summary = entry.get("summary", "")
            parts = [p.strip() for p in summary.split(" - ")]
            location = ""
            salary = ""
            for part in parts:
                lower = part.lower()
                if any(w in lower for w in ["remote", "worldwide", "global", "anywhere"]):
                    location = part
                elif "$" in part or "/yr" in part or "/mo" in part:
                    salary = part

            if not location:
                location = "Remote"  # remote3.co is remote-only

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
                )
            )
        print(f"[remote3.co] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
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
        resp = httpx.get(
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
                )
            )
        print(f"[cryptojobslist.com/web3] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[cryptojobslist.com/web3] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 6: Greenhouse ATS — public JSON API, no auth required
# Covers: Coinbase, Consensys, Alchemy, Ripple, Fireblocks, BitGo, Gemini,
#         Nansen, Avalabs, Paradigm, Messari, Figment, Solana Foundation
# ---------------------------------------------------------------------------

GREENHOUSE_COMPANIES = [
    ("coinbase",   "Coinbase"),
    ("consensys",  "Consensys"),
    ("alchemy",    "Alchemy"),
    ("ripple",     "Ripple"),
    ("fireblocks", "Fireblocks"),
    ("bitgo",      "BitGo"),
    ("gemini",     "Gemini"),
    ("nansen",     "Nansen"),
    ("avalabs",    "Ava Labs"),
    ("paradigm",   "Paradigm"),
    ("messari",    "Messari"),
    ("figment",    "Figment"),
    ("solana",     "Solana Foundation"),
]


def fetch_greenhouse() -> list[Job]:
    """Query Greenhouse public job board API for verified web3 companies."""
    all_jobs: list[Job] = []
    for slug, company_name in GREENHOUSE_COMPANIES:
        try:
            resp = httpx.get(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                headers=HEADERS,
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            for j in resp.json().get("jobs", []):
                url = j.get("absolute_url", "")
                if not url:
                    continue
                title = html.unescape(j.get("title", "").strip())
                location = j.get("location", {}).get("name", "")
                posted = j.get("first_published", "")
                all_jobs.append(
                    Job(
                        id=_make_id(url),
                        title=title,
                        company=company_name,
                        location=location,
                        url=url,
                        source=f"greenhouse/{slug}",
                        posted=posted,
                    )
                )
        except Exception as e:
            print(f"[greenhouse/{slug}] ERROR: {e}")
    print(f"[greenhouse] {len(all_jobs)} jobs fetched across {len(GREENHOUSE_COMPANIES)} companies")
    return all_jobs


# ---------------------------------------------------------------------------
# Board 7: Lever ATS — public JSON API, no auth required
# Covers: Binance, 1inch, CertiK, Anchorage, Ledger
# ---------------------------------------------------------------------------

LEVER_COMPANIES = [
    ("binance",   "Binance"),
    ("1inch",     "1inch"),
    ("certik",    "CertiK"),
    ("anchorage", "Anchorage Digital"),
    ("ledger",    "Ledger"),
]


def fetch_lever() -> list[Job]:
    """Query Lever public posting API for verified web3 companies."""
    all_jobs: list[Job] = []
    for slug, company_name in LEVER_COMPANIES:
        try:
            resp = httpx.get(
                f"https://api.lever.co/v0/postings/{slug}?mode=json",
                headers=HEADERS,
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            for j in resp.json():
                url = j.get("hostedUrl", "")
                if not url:
                    continue
                title = html.unescape(j.get("text", "").strip())
                cats = j.get("categories", {})
                location = cats.get("location", "")
                workplace = j.get("workplaceType", "")
                if workplace == "remote" and not location:
                    location = "Remote"
                posted = str(j.get("createdAt", ""))
                all_jobs.append(
                    Job(
                        id=_make_id(url),
                        title=title,
                        company=company_name,
                        location=location,
                        url=url,
                        source=f"lever/{slug}",
                        posted=posted,
                    )
                )
        except Exception as e:
            print(f"[lever/{slug}] ERROR: {e}")
    print(f"[lever] {len(all_jobs)} jobs fetched across {len(LEVER_COMPANIES)} companies")
    return all_jobs


# ---------------------------------------------------------------------------
# Board 8: blockace.io — HTML scraping, all remote web3 jobs
# ---------------------------------------------------------------------------

def fetch_blockace() -> list[Job]:
    """Scrape blockace.io job listings."""
    base = "https://blockace.io"
    try:
        resp = httpx.get(base, headers=HEADERS, follow_redirects=True, timeout=20)
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
            location = loc_el.get_text(strip=True) if loc_el else "Remote"

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
                )
            )
        print(f"[blockace.io] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
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
        resp = httpx.get(
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
            location = j.get("location", "Remote") or "Remote"
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
                )
            )
        print(f"[remoteok.com] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[remoteok.com] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 10: crypto.jobs — HTML scraping
# ---------------------------------------------------------------------------

def fetch_cryptojobs() -> list[Job]:
    """Scrape crypto.jobs job listing page."""
    base = "https://crypto.jobs"
    try:
        resp = httpx.get(
            f"{base}/jobs",
            headers=HEADERS,
            follow_redirects=True,
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        for card in soup.select("a.job-url"):
            href = card.get("href", "")
            url = href if href.startswith("http") else base + href

            title_el = card.select_one("p.job-title")
            title = html.unescape(title_el.get_text(strip=True)) if title_el else ""

            # Company is the <span> directly after the <p>
            spans = card.find_all("span", recursive=False)
            company = html.unescape(spans[0].get_text(strip=True)) if spans else ""
            if not company:
                # fallback: find first non-empty span
                for s in card.find_all("span"):
                    t = s.get_text(strip=True)
                    if t and len(t) < 80:
                        company = t
                        break

            # Location is inside the <small> block
            location = ""
            small = card.select_one("div.hidden-xs small")
            if small:
                for span in small.find_all("span"):
                    t = span.get_text(strip=True)
                    if "🌍" in t or "remote" in t.lower():
                        location = t.replace("🌍", "").strip()
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
                )
            )
        print(f"[crypto.jobs] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
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
        resp = httpx.get(
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

            url = ext_links[0]
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
                    location="",  # not in post; filters will pass unknowns through
                    url=url,
                    source="@web3hiring",
                )
            )

        print(f"[@web3hiring] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[@web3hiring] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 12: Lever — Aave Labs (EU endpoint)
# ---------------------------------------------------------------------------

def fetch_lever_aave() -> list[Job]:
    """Aave Labs uses the EU Lever endpoint."""
    try:
        resp = httpx.get(
            "https://api.eu.lever.co/v0/postings/aavelabs?mode=json",
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[lever/aave] {resp.status_code}")
            return []
        jobs = []
        for j in resp.json():
            url = j.get("hostedUrl", "")
            if not url:
                continue
            title = html.unescape(j.get("text", "").strip())
            cats = j.get("categories", {})
            location = cats.get("location", "")
            workplace = j.get("workplaceType", "")
            if workplace == "remote" and not location:
                location = "Remote"
            jobs.append(
                Job(
                    id=_make_id(url),
                    title=title,
                    company="Aave Labs",
                    location=location,
                    url=url,
                    source="lever/aave",
                    posted=str(j.get("createdAt", "")),
                )
            )
        print(f"[lever/aave] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[lever/aave] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 13: @cryptojobsdaily Telegram — structured "Company/Title/Location" posts
# Posts are formatted: Company: X \n Title: Y \n Location: Z \n Apply Here: URL
# ---------------------------------------------------------------------------

def fetch_cryptojobsdaily_telegram() -> list[Job]:
    """Scrape the @cryptojobsdaily public Telegram channel (structured job posts)."""
    try:
        resp = httpx.get(
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
            url = ext_links[0] if ext_links else ""
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
                )
            )

        print(f"[@cryptojobsdaily] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[@cryptojobsdaily] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Board 14: @cryptojobslist Telegram — emoji-delimited structured posts
# Posts: 💼 title  🏛️ at company  🌍 location  💰 salary  ✅ Apply → URL
# ---------------------------------------------------------------------------

def fetch_cryptojobslist_telegram() -> list[Job]:
    """Scrape the @cryptojobslist public Telegram channel (emoji-structured posts)."""
    try:
        resp = httpx.get(
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
            url = ext_links[0] if ext_links else ""
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
                )
            )

        print(f"[@cryptojobslist] {len(jobs)} jobs fetched")
        return jobs
    except Exception as e:
        print(f"[@cryptojobslist] ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# X.com hiring posts via Bing search
# Note: X/Twitter blocked all search engine crawlers in 2023, so this
# returns 0 results in practice. Kept as a canary — if X ever re-enables
# indexing it will start working automatically.
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
    "site:x.com \"we're hiring\" web3 marketing",
    "site:x.com \"we're hiring\" crypto community manager OR growth OR content",
    "site:x.com \"hiring\" web3 \"head of marketing\" OR \"marketing lead\" OR \"brand\"",
]


def fetch_twitter_bing() -> list[Job]:
    """Search Bing for recent X.com posts about web3 marketing hiring (last week)."""
    jobs = []
    seen_urls: set[str] = set()

    for query in _TWITTER_QUERIES:
        try:
            resp = httpx.get(
                "https://www.bing.com/search",
                params={"q": query, "freshness": "Week"},
                headers=_BING_HEADERS,
                timeout=15,
                follow_redirects=True,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for result in soup.select("li.b_algo"):
                link = result.select_one("h2 a")
                if not link:
                    continue
                url = link.get("href", "")
                if not url or url in seen_urls:
                    continue
                if "x.com" not in url and "twitter.com" not in url:
                    continue
                # Only tweet URLs (contain /status/)
                if "/status/" not in url:
                    continue
                seen_urls.add(url)

                # Bing h2 for tweets: 'Handle on X: "tweet text..."'
                h2_text = link.get_text(strip=True)
                tweet_match = re.search(r' on X: [\u201c"](.*)', h2_text, re.DOTALL)
                if tweet_match:
                    title = tweet_match.group(1).rstrip('\u201d"').strip()
                else:
                    snippet_el = result.select_one(".b_caption p")
                    title = snippet_el.get_text(strip=True) if snippet_el else h2_text
                title = title[:200]  # cap length

                # Extract @handle from URL path
                handle_match = re.search(
                    r"(?:x\.com|twitter\.com)/([^/?#]+)/status", url
                )
                company = f"@{handle_match.group(1)}" if handle_match else "X/Twitter"

                jobs.append(
                    Job(
                        id=_make_id(url),
                        title=title,
                        company=company,
                        location="Remote",
                        url=url,
                        source="x.com",
                    )
                )
        except Exception as e:
            print(f"[twitter/bing] ERROR ({query[:40]}): {e}")

    print(f"[twitter/bing] {len(jobs)} posts found")
    return jobs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

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
    fetch_twitter_bing,
]


def _title_company_key(job: Job) -> str:
    """Normalised key to catch the same job listed on multiple boards."""
    t = html.unescape(job.title or "").lower().strip()
    c = html.unescape(job.company or "").lower().strip()
    # strip common suffixes that vary between boards, e.g. "(remote)", "(global - remote)"
    import re as _re
    t = _re.sub(r"\s*\(.*?\)\s*$", "", t).strip()
    return f"{t}||{c}"


def fetch_all() -> list[Job]:
    all_jobs: list[Job] = []
    seen_ids: set[str] = set()
    seen_title_company: set[str] = set()
    for board_fn in BOARDS:
        for job in board_fn():
            tc_key = _title_company_key(job)
            if job.id in seen_ids or tc_key in seen_title_company:
                continue
            all_jobs.append(job)
            seen_ids.add(job.id)
            seen_title_company.add(tc_key)
    return all_jobs

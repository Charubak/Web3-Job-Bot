"""Free public ATS feeds. GET only, no accounts, keys or application actions.

Workable: https://workable.readme.io/reference/jobs-1
Teamtailor: https://support.teamtailor.com/en/articles/11171756-rss-feed-how-to-guide
Publication and creation timestamps do not establish original publication dates.
Requests use boards._get's shared byte, timeout, redirect and concurrency caps.
"""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import boards
from direct_sources import _CLOSED
from filters import _parse_posted_date
from source_health import record_failure, record_warning

# Official company careers pages verified 2026-11-08. These authenticate the
# board association only: they do NOT establish employer HQ or hiring eligibility.
WORKABLE_COMPANIES = (("walletconnect", "Reown"),)
WORKABLE_ASSOCIATION_SOURCES = {"walletconnect": "https://reown.com/careers"}
TEAMTAILOR_COMPANIES = (("crossmint.na.teamtailor.com", "Crossmint"),)
TEAMTAILOR_ASSOCIATION_SOURCES = {
    "crossmint.na.teamtailor.com": "https://www.crossmint.com/careers",
}
MAX_WORKABLE_ROWS = 1000
MAX_TEAMTAILOR_PAGES = 3
TEAMTAILOR_PAGE_SIZE = 100
TT = "{https://teamtailor.com/locations}"


def _text(value):
    return boards._plain_text(value) if isinstance(value, str) else ""


def _join(values, separator="; "):
    return separator.join(dict.fromkeys(value for value in values if value))


def _earliest(*values):
    valid = [(parsed, value) for value in values
             if (parsed := _parse_posted_date(value)) is not None]
    return min(valid, key=lambda pair: pair[0])[1] if valid else ""


def _merge(jobs, job):
    """A requisition may repeat for each location; never keep only the first."""
    old = jobs.get(job.id)
    if old is None:
        jobs[job.id] = job
        return
    old.location = _join([old.location, job.location])
    old.ats_country = _join([old.ats_country, job.ats_country])
    old.description = _join([old.description, job.description], "\n")
    old.published_at_upper_bound = _earliest(old.published_at_upper_bound,
                                            job.published_at_upper_bound)
    if old.title != job.title:
        old.description = _join([old.description, "Alternate title: " + job.title], "\n")
        record_warning("ats_duplicate_title_conflict")


def _explicitly_not_open(row):
    if _CLOSED.search(_text(row.get("description"))):
        return True
    if any(row.get(key) is True or _text(row.get(key)).lower() == "true"
           for key in ("closed", "archived", "is_archived", "isClosed")):
        return True
    if any(row.get(key) is False or _text(row.get(key)).lower() == "false"
           for key in ("isPublished", "published", "is_open")):
        return True
    for key in ("state", "status"):
        # Workable's documented state field is geographic, so only status
        # or recognizable closure terms in state count as lifecycle evidence.
        value = _text(row.get(key)).lower()
        if value in {"closed", "archived", "draft", "unpublished", "expired", "filled", "cancelled", "canceled"}:
            return True
    return False


def _workable_job(row, slug, company):
    if not isinstance(row, dict):
        raise ValueError("Invalid Workable item")
    shortcode, title = row.get("shortcode"), _text(row.get("title"))
    if not isinstance(shortcode, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", shortcode) or not title:
        raise ValueError("Missing Workable job identity")
    locations = row.get("locations", [])
    if not isinstance(locations, list) or any(not isinstance(loc, dict) for loc in locations):
        raise ValueError("Invalid Workable locations")
    addresses = [row] + locations
    location = [_join([_text(loc.get(key)) for key in
                      ("name", "city", "state", "region", "country", "countryCode")], ", ")
                for loc in addresses]
    workplace = _text(row.get("workplace_type"))
    if workplace:
        location.insert(0, {"remote": "Remote", "hybrid": "Hybrid", "on_site": "On-site"}.get(workplace, workplace))
    elif row.get("telecommuting") is True:
        location.insert(0, "Remote")
    url = f"https://apply.workable.com/j/{shortcode}"
    description = _text(row.get("description"))
    if not description:
        record_warning("ats_missing_description")
    return boards.Job(
        id=boards._make_id(url), title=title, company=company, location=_join(location),
        url=url, source=f"workable/{slug}", description=description, posted="",
        # created_at describes draft creation, not existence as a public opening.
        published_at_upper_bound=_earliest(_text(row.get("published_on"))),
        current_open_verified=bool(description),
        ats_country=_join([_text(loc.get(key)) for loc in addresses for key in ("country", "countryCode")]),
    )


def fetch_workable():
    jobs = {}
    closed_ids = set()
    for slug, company in WORKABLE_COMPANIES:
        try:
            response = boards._get(f"https://www.workable.com/api/accounts/{slug}?details=true",
                                   follow_redirects=True, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise ValueError("Invalid Workable public jobs response")
            rows = payload["jobs"]
            if len(rows) > MAX_WORKABLE_ROWS:
                record_warning("bounded_workable_row_cap")
            record_warning("ats_original_posting_dates_unverified")
            for row in rows[:MAX_WORKABLE_ROWS]:
                try:
                    if isinstance(row, dict) and _explicitly_not_open(row):
                        record_warning("ats_closed_item_omitted")
                        code = row.get("shortcode")
                        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", code):
                            identity = boards._make_id(f"https://apply.workable.com/j/{code}")
                            closed_ids.add(identity)
                            jobs.pop(identity, None)
                        continue
                    job = _workable_job(row, slug, company)
                    if job.id not in closed_ids:
                        _merge(jobs, job)
                except (ValueError, TypeError):
                    record_failure()
                    record_warning("ats_invalid_item")
        except Exception as exc:
            record_failure()
            print(f"[workable/{slug}] ERROR: {type(exc).__name__}")
    return list(jobs.values())


def _teamtailor_job(item, host, company):
    title = _text(item.findtext("title"))
    url = (item.findtext("link") or "").strip()
    parts = urlsplit(url)
    if not (title and parts.scheme == "https" and parts.hostname == host
            and not parts.username and not parts.password and parts.port in (None, 443)
            and re.fullmatch(r"/jobs/\d+[^/]*/*", parts.path)):
        raise ValueError("Invalid Teamtailor posting URL")
    locations = item.findall(f"{TT}locations/{TT}location")
    address_parts = [_join([_text(loc.findtext(TT + key)) for key in
                           ("name", "address", "zip", "city", "region", "country")], ", ")
                     for loc in locations]
    remote = _text(item.findtext("remoteStatus"))
    workplace = {"fully": "Remote", "hybrid": "Hybrid", "none": "On-site"}.get(remote, remote)
    description = _text(item.findtext("description"))
    if not description:
        record_warning("ats_missing_description")
    return boards.Job(
        id=boards._make_id(url),
        title=title, company=company, location=_join([workplace] + address_parts),
        url=boards.canonical_job_url(url), source=f"teamtailor/{host}",
        description=description, posted="", current_open_verified=bool(description),
        published_at_upper_bound=_earliest(_text(item.findtext("pubDate"))),
        ats_country=_join([_text(loc.findtext(TT + "country")) for loc in locations]),
    )


def fetch_teamtailor():
    jobs = {}
    closed_ids = set()
    for host, company in TEAMTAILOR_COMPANIES:
        seen_pages = set()
        for page in range(MAX_TEAMTAILOR_PAGES):
            try:
                url = f"https://{host}/jobs.rss?offset={page * TEAMTAILOR_PAGE_SIZE}&per_page={TEAMTAILOR_PAGE_SIZE}"
                response = boards._get(url, follow_redirects=True, timeout=15)
                response.raise_for_status()
                body = response.content
                # Reject DTDs/entities before XML parsing, including UTF-16 text.
                if b"<!doctype" in body.lower().replace(b"\x00", b"") or b"<!entity" in body.lower().replace(b"\x00", b""):
                    raise ValueError("Unexpected feed DTD")
                root = ET.fromstring(body)
                if root.tag != "rss" or root.find("channel") is None:
                    raise ValueError("Invalid Teamtailor RSS response")
                items = root.findall("./channel/item")
                signature = tuple(ET.tostring(item) for item in items)
                if signature and signature in seen_pages:
                    record_warning("bounded_teamtailor_pagination_repeated")
                    break
                seen_pages.add(signature)
                record_warning("ats_original_posting_dates_unverified")
                if len(items) > TEAMTAILOR_PAGE_SIZE:
                    record_warning("bounded_teamtailor_page_size_ignored")
                for item in items[:TEAMTAILOR_PAGE_SIZE]:
                    try:
                        job = _teamtailor_job(item, host, company)
                        if _explicitly_not_open({key: item.findtext(key) for key in ("state", "status", "closed", "archived", "is_archived", "isClosed", "isPublished", "published", "is_open", "description")}):
                            record_warning("ats_closed_item_omitted")
                            closed_ids.add(job.id)
                            jobs.pop(job.id, None)
                            continue
                        if job.id not in closed_ids:
                            _merge(jobs, job)
                    except (ValueError, TypeError):
                        record_failure()
                        record_warning("ats_invalid_item")
                if len(items) < TEAMTAILOR_PAGE_SIZE:
                    break
                if page == MAX_TEAMTAILOR_PAGES - 1:
                    record_warning("bounded_teamtailor_page_cap")
            except Exception as exc:
                record_failure()
                print(f"[teamtailor/{host}] ERROR: {type(exc).__name__}")
                break
    return list(jobs.values())

"""Free, read-only adapters for independently verified company careers sources.

Ashby's public API lists currently published postings, but its ``publishedAt``
means *last* publication, not original publication. Never use it for Job.posted.
Documentation: https://developers.ashbyhq.com/docs/public-job-posting-api
All requests share boards._get's response, time and concurrency limits.
"""

import html
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

import boards


# Official company pages were checked on 2026-10-19 and link to these boards.
# These are source associations only, never employer-HQ or eligibility evidence.
# Board names are case-sensitive; do not guess slugs from company names.
ASHBY_COMPANIES = (
    ("trust-wallet", "Trust Wallet"),
    ("allium", "Allium"),
    ("keyrock", "Keyrock"),
    ("ether.fi", "Ether.fi"),
    ("Lido.fi", "Lido"),
    ("Injective", "Injective"),
    ("morpho", "Morpho"),
)
ASHBY_ASSOCIATION_SOURCES = {
    "trust-wallet": "https://trustwallet.com/careers",
    "allium": "https://www.allium.so/about",
    "keyrock": "https://keyrock.com/careers/",
    "ether.fi": "https://www.ether.fi/careers",
    "Lido.fi": "https://lido.fi/opportunities/strategic-project-manager-operations",
    "Injective": "https://injective.com/",
    "morpho": "https://morpho.org/jobs/ashby-head-of-security-ed97697e-2dd2-4b9e-b8b4-1a3d9c288db9",
}

GAINS_INDEX_URL = "https://hiring.gains.trade/"
GAINS_JOB_URL = "https://hiring.gains.trade/roles/head-of-growth/"
HACKEN_JOB_URL = "https://hacken.io/careers/175289-growth-marketing-manager/"
# The employer's archived DOU advertisement, checked 2026-10-19, has the
# matching role title, full remit, requirements, KPIs and benefits. Its date is
# conservative evidence that this role already existed, not an original-date
# claim. Keep this known-old URL excluded even while its Apply CTA remains.
HACKEN_AGE_EVIDENCE_URL = "https://jobs.dou.ua/companies/hacken/vacancies/338824/"
HACKEN_EXISTED_BY = "2026-01-06"

_CLOSURE_AUX = (
    r"(?:(?:is|are|was|were|has|have)\s+)?"
    r"(?:(?:now|currently|already|recently)\s+)?(?:been\s+)?"
    r"(?:(?:now|currently|already|recently)\s+)?"
)
_CLOSED = re.compile(
    r"\b(?:job(?: posting)?|role|position|vacancy|opening)\s+" + _CLOSURE_AUX +
    r"(?:closed|filled|removed|expired|no longer (?:available|open))\b|"
    r"\bapplications?\s+" + _CLOSURE_AUX + r"closed\b|"
    r"\b(?:no longer|not|stopped)\s+accepting\s+(?:new\s+)?applications?\b|"
    r"\b(?:no longer|not)\s+hiring\s+for\s+this\s+(?:role|position)\b",
    re.I,
)


def _text(value) -> str:
    return html.unescape(value).strip() if isinstance(value, str) else ""


def _unique(parts) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in parts if part and part.strip()))


def _address_parts(address) -> list[str]:
    if not isinstance(address, dict):
        return []
    # The docs show a flat secondary address; live responses also nest it.
    nested = address.get("postalAddress")
    parts = [_text(address.get(key)) for key in
             ("addressLocality", "addressRegion", "addressCountry")]
    if isinstance(nested, dict):
        parts.extend(_text(nested.get(key)) for key in
                     ("addressLocality", "addressRegion", "addressCountry"))
    return _unique(parts)


def _ashby_location(posting: dict) -> str:
    parts = [_text(posting.get("location"))]
    parts.extend(_address_parts(posting.get("address")))
    secondary = posting.get("secondaryLocations") or []
    if isinstance(secondary, list):
        for location in secondary:
            if isinstance(location, dict):
                parts.append(_text(location.get("location")))
                parts.extend(_address_parts(location.get("address")))
            elif isinstance(location, str):
                parts.append(location)
    workplace = _text(posting.get("workplaceType"))
    if workplace:
        parts.append(workplace)
    if posting.get("isRemote") is True:
        parts.append("Remote")
    return "; ".join(_unique(parts))


def _ashby_description(posting: dict) -> str:
    # Preserve both representations when they differ: a truncated plain-text
    # summary must not discard residency restrictions present only in the HTML.
    plain = _text(posting.get("descriptionPlain"))
    rich = boards._plain_text(_text(posting.get("descriptionHtml")))
    parts = [plain]
    if rich and re.sub(r"\s+", " ", rich) != re.sub(r"\s+", " ", plain):
        parts.append(rich)
    employment = _text(posting.get("employmentType"))
    if employment:
        parts.append(f"Employment type: {employment}")
    if posting.get("isRemote") is False:
        parts.append("Remote work is unavailable (source isRemote=false).")
    published = _text(posting.get("publishedAt"))
    if published:
        parts.append(f"Ashby last published: {published}. Original posting date unverified.")
    return "\n\n".join(_unique(parts))


def _ashby_job_url(url: str, slug: str) -> bool:
    try:
        parts = urlsplit(url)
        path = parts.path.strip("/").split("/")
        return (parts.scheme == "https" and parts.hostname == "jobs.ashbyhq.com"
                and parts.port in (None, 443) and not parts.username and not parts.password
                and len(path) == 2 and path[0] == slug
                and bool(re.fullmatch(r"[A-Za-z0-9_-]+", path[1])))
    except ValueError:
        return False


def _fetch_ashby_one(slug: str, company_name: str) -> list[boards.Job]:
    try:
        response = boards._get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            params={"includeCompensation": "true"}, timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Expected an Ashby jobs list")
        jobs = []
        for posting in payload["jobs"]:
            if not isinstance(posting, dict) or posting.get("isListed") is not True:
                continue
            # Only published public jobs are returned by this endpoint. If an
            # explicit contradictory status is present, fail closed anyway.
            if posting.get("isOpen") is False or posting.get("isArchived") is True:
                continue
            status = _text(posting.get("status")).lower()
            if status and status not in {"published", "open", "active"}:
                continue
            title = _text(posting.get("title"))
            url = _text(posting.get("jobUrl"))
            description = _ashby_description(posting)
            if not title or not _ashby_job_url(url, slug) or _CLOSED.search(description):
                continue
            # Descriptive metadata alone is not a complete job posting.
            if not (_text(posting.get("descriptionPlain")) or
                    boards._plain_text(_text(posting.get("descriptionHtml")))):
                continue
            compensation = posting.get("compensation") or {}
            salary = ""
            if isinstance(compensation, dict):
                salary = (_text(compensation.get("compensationTierSummary")) or
                          _text(compensation.get("scrapeableCompensationSalarySummary")))
            jobs.append(boards.Job(
                id=boards._make_id(url), title=title, company=company_name,
                location=_ashby_location(posting), url=url, source=f"ashby/{slug}",
                salary=salary, posted="", description=description,
                published_at_upper_bound=_text(posting.get("publishedAt")),
            ))
        return jobs
    except Exception as exc:
        print(f"[ashby/{slug}] ERROR: {exc}")
        return []


def fetch_ashby() -> list[boards.Job]:
    """Fetch verified public boards sequentially, with no auth or nested pool."""
    jobs = []
    for slug, company in ASHBY_COMPANIES:
        jobs.extend(_fetch_ashby_one(slug, company))
    print(f"[ashby] {len(jobs)} jobs fetched across {len(ASHBY_COMPANIES)} companies")
    return jobs


def _same_company_page(url: str, expected: str) -> bool:
    try:
        parts, target = urlsplit(url), urlsplit(expected)
        return (parts.scheme == "https" and parts.hostname == target.hostname
                and parts.port in (None, 443) and not parts.username and not parts.password
                and parts.path.rstrip("/") == target.path.rstrip("/"))
    except ValueError:
        return False


def _visible_soup(page: str) -> BeautifulSoup:
    soup = BeautifulSoup(page, "html.parser")
    for tag in soup.select("script, style, svg, template, [hidden], [aria-hidden='true']"):
        tag.decompose()
    for tag in soup.select("[style]"):
        if tag.attrs is not None and re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", tag.get("style", ""), re.I):
            tag.decompose()
    return soup


def fetch_gains() -> list[boards.Job]:
    """Require the live careers listing, role body and active application form.

    A stale page, generic HTTP 200 error, redirect to the index, missing form or
    explicit closure is not sufficient evidence that this role remains open.
    No form is submitted and no application-intake endpoint is contacted.
    """
    try:
        index = boards._get(GAINS_INDEX_URL, follow_redirects=True, timeout=12)
        index.raise_for_status()
        if not _same_company_page(str(index.url), GAINS_INDEX_URL):
            return []
        index_soup = _visible_soup(index.text)
        role_link = next((a for a in index_soup.select("a.role[href]")
                          if _same_company_page(urljoin(GAINS_INDEX_URL, a["href"]), GAINS_JOB_URL)), None)
        if role_link is None or _CLOSED.search(role_link.get_text(" ", strip=True)):
            return []
        response = boards._get(GAINS_JOB_URL, follow_redirects=True, timeout=12)
        response.raise_for_status()
        if not _same_company_page(str(response.url), GAINS_JOB_URL):
            return []
        soup = _visible_soup(response.text)
        heading = soup.select_one("h1")
        eyebrow = soup.select_one(".hero .eyebrow")
        form = soup.select_one("form#applyForm[data-role]")
        if heading is None or eyebrow is None or form is None:
            return []
        meta = eyebrow.get_text(" ", strip=True)
        form_role = _text(form.get("data-role"))
        if not re.search(r"\bopen role\b", meta, re.I) or not re.search(r"\bhead of growth\b", form_role, re.I):
            return []
        submit = form.select_one("button[type='submit']")
        if (submit is None or submit.has_attr("disabled") or submit.get("aria-disabled") == "true"
                or form.select_one("input[name='name']") is None
                or form.select_one("input[name='telegram']") is None):
            return []
        for br in heading.find_all("br"):
            br.replace_with(" / ")
        title = heading.get_text(" ", strip=True).rstrip(".")
        if not re.search(r"\bhead of growth\b", title, re.I):
            return []
        # Keep the entire visible role body, including any new requirements
        # section and application eligibility text. Navigation is not the role.
        for tag in soup.select("nav, footer"):
            tag.decompose()
        description = (soup.body or soup).get_text(" ", strip=True)
        if _CLOSED.search(description) or soup.select_one("section#mandate") is None:
            return []
        # Keep every metadata segment, not just Remote: an added country or
        # hybrid condition must survive. 'International' is not 'worldwide'.
        location = " · ".join(part.strip() for part in re.split(r"[·|]", meta)
                              if part.strip() and part.strip().lower() != "open role")
        job = boards.Job(
            id=boards._make_id(GAINS_JOB_URL), title=title, company="Gains",
            location=location, url=GAINS_JOB_URL, source="company/gains.trade",
            posted="", description=description,
        )
        print("[company/gains.trade] 1 open role fetched; original posting date unknown")
        return [job]
    except Exception as exc:
        print(f"[company/gains.trade] ERROR: {exc}")
        return []


def fetch_hacken() -> list[boards.Job]:
    """Read one verified company role; an enabled Apply button is required.

    The live careers index is client-rendered, so this narrowly scoped adapter
    reads the verified role's server-rendered description and application CTA.
    It never opens the application flow. Original date/HQ remain unknown; the
    matching historical advertisement supplies an exclusion-only age bound.
    """
    try:
        response = boards._get(HACKEN_JOB_URL, follow_redirects=True, timeout=12)
        response.raise_for_status()
        if not _same_company_page(str(response.url), HACKEN_JOB_URL):
            return []
        soup = _visible_soup(response.text)
        main = soup.select_one("main")
        if main is None:
            return []
        heading = main.select_one("h1")
        title = heading.get_text(" ", strip=True) if heading is not None else ""
        if title.casefold() != "growth marketing manager":
            return []
        apply_button = next((button for button in main.select("button")
                             if button.get_text(" ", strip=True).lower() == "apply now"
                             and not button.has_attr("disabled")
                             and button.get("aria-disabled") != "true"), None)
        if (apply_button is None or main.select_one(".jobDescription") is None
                or main.select_one(".jobRequirements") is None):
            return []
        description = main.get_text(" ", strip=True)
        if _CLOSED.search(description):
            return []
        metadata = [tag.get_text(" ", strip=True) for tag in
                    main.select("[class*='headerDescriptionDepartment']")]
        description += (
            f"\n\nMatching historical role advertised by {HACKEN_EXISTED_BY}: "
            f"{HACKEN_AGE_EVIDENCE_URL}. Exclusion-only age evidence; original "
            "posting date and current application acceptance remain unverified."
        )
        job = boards.Job(
            id=boards._make_id(HACKEN_JOB_URL), title=title, company="Hacken",
            location="; ".join(_unique(metadata)), url=HACKEN_JOB_URL,
            source="company/hacken.io", posted="", description=description,
            published_at_upper_bound=HACKEN_EXISTED_BY,
        )
        print("[company/hacken.io] 1 role with application CTA fetched; known-old age bound retained")
        return [job]
    except Exception as exc:
        print(f"[company/hacken.io] ERROR: {exc}")
        return []

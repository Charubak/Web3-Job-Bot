"""Bounded, live-only adapters for a small set of verified careers URLs.

No cached descriptions, guessed publication dates or application submissions.
The first three sources were inspected as actual HTTP HTML on 2026-10-19.
The Group Three currently exposes a short listing, so it is deliberately not
emitted until a complete, independently scoped role description is present.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

import boards
from direct_sources import _CLOSED, _same_company_page, _visible_soup


DEFUSE_URL = "https://job-boards.eu.greenhouse.io/defuselabs/jobs/4997160101"
PANCAKESWAP_URL = "https://talent.yzilabs.com/jobs/c2ce0aad-fee1-49a3-9abc-b6ad32480795"
INTERCEPTA_URL = "https://intercepta.io/careers/marketing-marketing-lead-remote"
GROUP_THREE_URL = "https://www.thegroupthree.com/careers/"
GETBLOCK_URL = "https://getblock.factorial.com/job_posting/full-stack-marketing-manager-marketing-pmm-327722"
GETBLOCK_APPLY_URL = "https://getblock.factorial.com/apply/full-stack-marketing-manager-marketing-pmm-327722"
TYB_SOGAL_URL = "https://jobs.sogalventures.com/companies/tyb/jobs/95076705-lifecycle-marketing-lead"
TYB_COSIGN_URL = "https://cosign.co/jobs/0jw5ou60xx"
TYB_APPLY_URL = "https://tryyourbest.notion.site/Lifecycle-Marketing-Lead-3e433d6ad08980be9740ce153507e87b"
# Verified prior existence, retained if an aggregator refreshes its timestamp.
TYB_EXISTED_BY = "2026-09-29"
TYB_TITLE = "Lifecycle Marketing, Lead"
_GLOBAL_LOCATION_PLACEHOLDERS = frozenset({
    'earth', 'worldwide', 'global', 'anywhere', 'all countries', 'any country',
})


@dataclass(frozen=True)
class RoleSource:
    url: str
    company: str
    title: str
    sections: tuple[str, ...]
    original_date_authority: bool = True
    index: bool = False


# Official YZi hiring post checked in the cloud browser on 2026-10-19:
# https://x.com/yzilabs/status/2109642046793564254 (12 Oct, 19:17 UTC).
# This proves existence by then, not the original date or a refreshed job age.
KNOWN_ROLE_EXISTENCE = {PANCAKESWAP_URL: "2026-10-12T19:17:00Z"}

ROLE_SOURCES = (
    RoleSource(DEFUSE_URL, "Defuse Labs", "Head of Product Marketing & Growth",
               ("About Defuse Labs", "About the Role", "Key Responsibilities", "Requirements")),
    RoleSource(PANCAKESWAP_URL, "PancakeSwap", "Head of Marketing",
               ("About the role", "Requirements"), original_date_authority=False),
    RoleSource(INTERCEPTA_URL, "Intercepta", "Marketing Lead",
               ("About Intercepta", "Role Overview", "Your Impact", "Requirements")),
    RoleSource(GROUP_THREE_URL, "The Group Three", "Senior Growth Strategist",
               ("Responsibilities", "Requirements"), index=True),
    RoleSource(GETBLOCK_URL, "GetBlock", "Full-Stack Marketing Manager / Marketing PMM",
               ("About GetBlock", "About the role", "What you'll do", "What you'll bring")),
)


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _disabled(tag) -> bool:
    return any(node.has_attr("disabled") or
               str(node.get("aria-disabled", "")).casefold() == "true"
               for node in (tag, *tag.parents) if isinstance(node, Tag))


def _application_form(root) -> bool:
    """A blank required-fields form may legitimately disable Submit.

    Verify enabled application fields and a submit control instead of treating
    initial validation/Turnstile state as a closed vacancy. A wholly disabled
    form, missing fields or a lone Apply button is insufficient evidence.
    """
    for form in root.select("form"):
        if _disabled(form):
            continue
        fields = [tag for tag in form.select("input, textarea") if not _disabled(tag)]
        email = any(tag.get("type") == "email" or tag.get("autocomplete") == "email"
                    or "email" in str(tag.get("id", "")).casefold() for tag in fields)
        name = any("name" in (str(tag.get("id", "")) + " " +
                             str(tag.get("name", ""))).casefold() for tag in fields)
        resume = any(tag.get("type") == "file" for tag in fields)
        submit = any(re.search(r"\b(?:submit|send) application\b",
                               tag.get_text(" ", strip=True), re.I)
                     for tag in form.select("button[type='submit']"))
        if email and name and resume and submit:
            return True
    return False


def _application_link(tags, expected_url: str) -> bool:
    for tag in tags:
        if not isinstance(tag, Tag) or tag.name != "a" or _disabled(tag):
            continue
        if not re.fullmatch(r"Apply(?: for this role| for this job| now)?\s*[→↗]?",
                            _normal(tag.get_text(" ", strip=True)), re.I):
            continue
        href = str(tag.get("href", ""))
        if expected_url == GETBLOCK_URL:
            # This requisition's exact official Apply link establishes that
            # the role is advertised. The application form is never visited,
            # tested or submitted. A different/old requisition cannot substitute.
            if _same_company_page(urljoin(expected_url, href), GETBLOCK_APPLY_URL):
                return True
            continue
        if href.startswith("mailto:") and "@" in href.partition("?")[0]:
            return True
        try:
            target = urlsplit(urljoin(expected_url, href))
            # Both employer pages currently use Cloudflare's public email
            # protection link. Its encrypted fragment must actually be present.
            if (target.scheme == "https" and not target.username and not target.password
                    and target.hostname == urlsplit(expected_url).hostname
                    and target.path == "/cdn-cgi/l/email-protection"
                    and re.fullmatch(r"[0-9a-fA-F]{12,}", target.fragment)):
                return True
        except ValueError:
            pass
    return False


def _heading_section(heading):
    """Return just one index role, ending at the next same/higher heading.

    Walking text nodes avoids accidentally copying an ancestor containing the
    following role. This also works when roles are nested in separate cards.
    """
    level = int(heading.name[1])
    nodes, text = [], [heading.get_text(" ", strip=True)]
    heading_children = {id(node) for node in heading.descendants}
    for node in heading.next_elements:
        if isinstance(node, Tag) and re.fullmatch(r"h[1-6]", node.name):
            if int(node.name[1]) <= level:
                break
        if id(node) in heading_children:
            continue
        nodes.append(node)
        if isinstance(node, NavigableString):
            value = _normal(str(node))
            if value:
                text.append(value)
    return "\n".join(text), nodes


def _json_nodes(value):
    if isinstance(value, list):
        for child in value:
            yield from _json_nodes(child)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _json_nodes(value["@graph"])


def _job_postings(page: str, source: RoleSource) -> list[dict]:
    postings = []
    soup = BeautifulSoup(page, "html.parser")
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for node in _json_nodes(payload):
            kind = node.get("@type")
            if kind != "JobPosting" and not (isinstance(kind, list) and "JobPosting" in kind):
                continue
            org = node.get("hiringOrganization")
            if not isinstance(org, dict) or not isinstance(org.get("name"), str):
                continue
            if _normal(org["name"]).casefold() != source.company.casefold():
                continue
            if not isinstance(node.get("title"), str) or _normal(node["title"]) != source.title:
                continue
            if node.get("url") and not _same_company_page(str(node["url"]), source.url):
                continue
            postings.append(node)
    return postings


def _iso_date(value) -> str:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T)", value):
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _iso_publication(value) -> str:
    """Validate without dropping a supplied time or UTC offset."""
    return value if _iso_date(value) else ""


def _oldest_publication(values) -> str:
    def instant(value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return min(filter(None, (_iso_publication(value) for value in values)), key=instant, default='')


def _structured_text(value) -> str:
    if isinstance(value, str):
        return boards._plain_text(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_structured_text(item) for item in value)))
    if isinstance(value, dict):
        return "; ".join(filter(None, (_structured_text(item) for key, item in value.items()
                                      if key not in {"@type", "@context", "@id"})))
    return ""


def _structured_description(posting) -> str:
    parts = []
    for key in ("description", "jobLocation", "jobLocationType", "applicantLocationRequirements",
                "eligibilityToWorkRequirement", "employmentType", "qualifications",
                "educationRequirements", "experienceRequirements", "responsibilities", "skills"):
        value = _structured_text(posting.get(key))
        if value:
            if key == 'applicantLocationRequirements':
                # A schema's applicant-location requirement is an actual
                # eligibility constraint. Express it in plain language so the
                # downstream geography classifier cannot miss camelCase data.
                # Getro also encodes its global placeholder as Country: Earth.
                # Keep that literal source data without inventing a residence
                # restriction or converting it to a country-eligibility claim.
                if value.casefold() in _GLOBAL_LOCATION_PLACEHOLDERS:
                    parts.append(f"Applicant location scope (source): {value}.")
                else:
                    parts.append(f"Applicants must be based in {value}.")
            else:
                parts.append(value if key == "description" else f"{key}: {value}")
    return "\n".join(parts)


def _job_location_values(value):
    """Extract role geography, never hiringOrganization/HQ address metadata."""
    if isinstance(value, str):
        if _normal(value):
            yield _normal(value)
    elif isinstance(value, list):
        for item in value:
            yield from _job_location_values(item)
    elif isinstance(value, dict):
        for key in ('name', 'address', 'addressLocality', 'addressRegion',
                    'addressCountry', 'streetAddress', 'postalCode'):
            yield from _job_location_values(value.get(key))


def _with_structured_locations(location, postings):
    parts = [location] if location else []
    for posting in postings:
        for value in _job_location_values(posting.get('jobLocation')):
            # A source's global placeholder is preserved in description. Do not
            # reinterpret it as a required residence or a confirmed country.
            if value.casefold() not in _GLOBAL_LOCATION_PLACEHOLDERS:
                parts.append(value)
        types = posting.get('jobLocationType')
        for value in types if isinstance(types, list) else [types]:
            raw = _structured_text(value)
            if not raw:
                continue
            normalized = re.sub(r'[^a-z]', '', raw.casefold())
            if normalized in {'onsite', 'inoffice', 'officebased', 'onpremises'}:
                parts.append('On-site')
            elif normalized == 'hybrid':
                parts.append('Hybrid')
            elif normalized in {'telecommute', 'remote'}:
                parts.append('Remote')
            elif raw.casefold() not in _GLOBAL_LOCATION_PLACEHOLDERS:
                # An unfamiliar workplace qualifier must remain reviewable.
                parts.append(raw)
    return '; '.join(dict.fromkeys(parts))


def _location(root, heading, source, section_text) -> str:
    if source.url == GETBLOCK_URL:
        # Factorial repeats the same role metadata above and beside the body.
        # Retain unknown fields in case they contain country/timezone limits.
        values = []
        for listing in root.select('.mb-8 > ul:not([class])'):
            for item in listing.find_all('li', recursive=False):
                value = _normal(item.get_text(' ', strip=True))
                if value and value.casefold() not in {'vendor / contractor', 'full time', 'marketing'}:
                    values.append(value)
        return '; '.join(dict.fromkeys(values))
    if source.url == DEFUSE_URL:
        location = root.select_one(".job__location")
        return location.get_text(" ", strip=True) if location else ""
    if source.url == PANCAKESWAP_URL:
        metadata = heading.find_next_sibling("p")
        if metadata:
            # Director is a verified seniority label, not a geographic region.
            # It remains in the complete description. Keep unknown/new metadata
            # because it could be a candidate location restriction.
            return "; ".join(part.strip() for part in metadata.get_text(" ", strip=True).split("·")
                             if part.strip().casefold() not in {source.company.casefold(), "director"})
    if source.url == INTERCEPTA_URL:
        for label in root.select("dt"):
            if _normal(label.get_text(" ", strip=True)).casefold() == "location":
                value = label.find_next_sibling("dd")
                if value:
                    return value.get_text(" ", strip=True)
    if source.index:
        # Exact role metadata only; the company's general remote-first copy is
        # never used as evidence for a role's work location.
        lines = section_text.splitlines()[1:]
        metadata = []
        for line in lines:
            if any(section.casefold() == line.casefold() for section in source.sections):
                break
            if not re.match(r"^Apply\b", line, re.I):
                metadata.append(line)
        return "; ".join(metadata)
    return ""


def _parse_role(page: str, source: RoleSource, *, now=None) -> list[boards.Job]:
    soup = _visible_soup(page)
    for tag in soup.select("nav, footer"):
        tag.decompose()
    root = soup.select_one("main") or soup.body or soup
    heading = next((tag for tag in root.select("h1, h2, h3, h4, h5, h6")
                    if _normal(tag.get_text(" ", strip=True)) == source.title), None)
    if heading is None:
        return []
    if source.url == GETBLOCK_URL:
        if _CLOSED.search(root.get_text(' ', strip=True)):
            return []
        # Select the smallest real role container containing its full body and
        # exact requisition Apply CTA. Exclude Factorial navigation/cookie UI.
        root = next((parent for parent in heading.parents if isinstance(parent, Tag)
                     and parent.select_one('.styledText') is not None
                     and _application_link(parent.select('a[href]'), source.url)), None)
        if root is None:
            return []
    full_text = root.get_text(" ", strip=True)
    if source.company.casefold() not in full_text.casefold():
        # Index identity can also appear in the site's own document title.
        document_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if not source.index or source.company.casefold() not in document_title.casefold():
            return []
    description, section_nodes = (_heading_section(heading) if source.index else
                                  (full_text, list(root.descendants)))
    labels = {_normal(tag.get_text(" ", strip=True)).rstrip(":").casefold()
              for tag in section_nodes if isinstance(tag, Tag) and
              tag.name in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"}}
    if len(description.split()) < 70 or not {section.casefold() for section in source.sections} <= labels:
        return []
    active = (_application_form(root) if source.url in {DEFUSE_URL, PANCAKESWAP_URL}
              else _application_link(section_nodes, source.url))
    if not active or _CLOSED.search(description):
        return []
    location = _location(root, heading, source, description)
    postings = _job_postings(page, source)
    location = _with_structured_locations(location, postings)
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    if any(_iso_date(posting.get("validThrough")) and
           _iso_date(posting["validThrough"]) < today for posting in postings):
        return []
    parts = [description]
    for posting in postings:
        structured = _structured_description(posting)
        if structured and structured not in description:
            parts.append(structured)
    description = "\n\n".join(parts)
    if _CLOSED.search(description):
        return []
    publication = _oldest_publication(posting.get("datePosted") for posting in postings)
    posted = publication if source.original_date_authority else ""
    # A dated investor-board listing proves prior existence, but does not
    # establish the employer's original publication. Never discard old evidence.
    upper_bound = _oldest_publication((publication if not source.original_date_authority else "",
                                         KNOWN_ROLE_EXISTENCE.get(source.url, "")))
    return [boards.Job(
        id=boards._make_id(source.url), title=source.title, company=source.company,
        location=location, url=source.url, source="company/curated", posted=posted,
        description=description, published_at_upper_bound=upper_bound, current_open_verified=True,
    )]


def _fetch_one(source: RoleSource) -> list[boards.Job]:
    try:
        response = boards._get(source.url, follow_redirects=True, timeout=12)
        response.raise_for_status()
        if not _same_company_page(str(response.url), source.url):
            return []
        return _parse_role(response.text, source)
    except Exception as exc:
        print(f"[company/curated/{source.company}] ERROR: {exc}")
        return []


def _parse_tyb(index_page: str, body_page: str, *, now=None) -> list[boards.Job]:
    """Pair a live investor listing with a full matching role body.

    SoGal supplies current listing/Apply evidence. Cosign's actual HTML includes
    a full JobPosting JSON-LD while the visible body streams through React.
    This does not claim that the official Notion application's body was read.
    Neither aggregator's date is asserted to be original employer publication.
    """
    soup = _visible_soup(index_page)
    for tag in soup.select("nav, footer"):
        tag.decompose()
    heading = next((tag for tag in soup.select("h1, h2, h3")
                    if _normal(tag.get_text(" ", strip=True)) == TYB_TITLE), None)
    if heading is None:
        return []
    index_text = soup.get_text(" ", strip=True)
    if (not re.search(r'\bTYB\b', index_text, re.I) or
            'Try Your Best' not in index_text or _CLOSED.search(index_text)):
        return []
    apply = next((tag for tag in soup.select('a[href]')
                  if _normal(tag.get_text(' ', strip=True)).casefold() == 'apply now'
                  and not _disabled(tag)
                  and _same_company_page(urljoin(TYB_SOGAL_URL, tag['href']), TYB_APPLY_URL)), None)
    if apply is None:
        return []
    # Preserve all role-header metadata before Apply, including any future
    # country or time-zone restriction, while removing only known labels.
    metadata = []
    heading_children = {id(node) for node in heading.descendants}
    for node in heading.next_elements:
        if node is apply:
            break
        if isinstance(node, NavigableString) and id(node) not in heading_children:
            value = _normal(str(node))
            parent_text = _normal(node.parent.get_text(' ', strip=True))
            if (value and value.casefold() not in {'tyb', 'marketing & communications'}
                    and not re.match(r'^Posted\b', parent_text, re.I)):
                metadata.append(value)
    cosign_source = RoleSource(TYB_COSIGN_URL, 'TYB', TYB_TITLE, (), False)
    index_source = RoleSource(TYB_SOGAL_URL, 'TYB', TYB_TITLE, (), False)
    postings = _job_postings(body_page, cosign_source)
    listing_postings = _job_postings(index_page, index_source)
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    if any(_iso_date(posting.get('validThrough')) and
           _iso_date(posting['validThrough']) < today for posting in postings + listing_postings):
        return []
    descriptions = [boards._plain_text(posting.get('description', '')) for posting in postings]
    if not descriptions or any(len(description.split()) < 100 or
                               any(label not in description for label in
                                   ('About TYB', 'About the Role', 'About You', 'Try Your Best'))
                               for description in descriptions):
        return []
    description = '\n\n'.join(dict.fromkeys(
        [index_text] + [_structured_description(posting) for posting in postings + listing_postings]))
    if _CLOSED.search(description):
        return []
    # Even when a refresh changes the visible board date, the already verified
    # Sep 29 existence bound remains. Older visible dates make it stricter.
    dates = [TYB_EXISTED_BY]
    dates.extend(posting.get('datePosted') for posting in postings + listing_postings)
    for value in re.findall(r'\bPosted on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})\b', index_text):
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                dates.append(datetime.strptime(value, fmt).date().isoformat())
                break
            except ValueError:
                pass
    return [boards.Job(
        id=boards._make_id(TYB_SOGAL_URL), title=TYB_TITLE, company='TYB',
        location=_with_structured_locations('; '.join(dict.fromkeys(metadata)),
                                            postings + listing_postings), url=TYB_SOGAL_URL,
        source='company/curated', posted='', description=description,
        published_at_upper_bound=_oldest_publication(dates), current_open_verified=True,
    )]


def fetch_tyb() -> list[boards.Job]:
    """At most two bounded public reads; no Notion login or application action."""
    try:
        pages = []
        for url in (TYB_SOGAL_URL, TYB_COSIGN_URL):
            response = boards._get(url, follow_redirects=True, timeout=12)
            response.raise_for_status()
            if not _same_company_page(str(response.url), url):
                return []
            pages.append(response.text)
        return _parse_tyb(*pages)
    except Exception as exc:
        print(f"[company/curated/TYB] ERROR: {exc}")
        return []


def fetch_company_roles() -> list[boards.Job]:
    """Read at most seven exact public URLs sequentially using shared bounds."""
    jobs = []
    for source in ROLE_SOURCES:
        jobs.extend(_fetch_one(source))
    jobs.extend(fetch_tyb())
    print(f"[company/curated] {len(jobs)} complete, currently open roles fetched")
    return jobs

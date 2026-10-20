"""Bounded, live-only ingestion of Hylo's verified Solana ecosystem listing.

The public role and its company profile were checked on 2026-10-19. This is
Hylo at hylo.so, the Solana DeFi protocol, not the unrelated hylo.com. A visible
listing date is evidence from this board, not a claim about an earlier employer
publication. No cached role text, invented dates or application submissions.
"""

import re
from datetime import datetime

import boards
from direct_sources import _CLOSED, _same_company_page, _visible_soup


HYLO_JOB_URL = "https://jobs.solana.com/companies/hylo-2/jobs/97132369-marketing-growth-lead"
HYLO_COMPANY_URL = "https://jobs.solana.com/companies/hylo-2"
HYLO_WEBSITE_URL = "https://hylo.so"
HYLO_TITLE = "Marketing & Growth Lead"
# Earliest verified dated listing, retained if this board later refreshes it.
# This is an existence upper bound, never a replacement for an unknown date.
HYLO_EXISTED_BY = "2026-10-15"


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _board_date(text: str) -> str:
    """Parse only the visible board's absolute 'Posted on' date.

    Relative, missing, invalid or updated dates remain unknown. If conflicting
    posted dates occur, keep the oldest rather than freshening an older role.
    """
    found = []
    for value in re.findall(r"\bPosted on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})\b", text, re.I):
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                found.append(datetime.strptime(_normal(value), fmt).date().isoformat())
                break
            except ValueError:
                pass
    return min(found, default="")


def _active_apply(soup) -> bool:
    for tag in soup.select("a, button, [role='button']"):
        if _normal(tag.get_text(" ", strip=True)).casefold() != "apply now":
            continue
        if (tag.has_attr("disabled") or tag.get("aria-disabled", "").casefold() == "true"
                or any(parent.has_attr("disabled") or
                       parent.get("aria-disabled", "").casefold() == "true"
                       for parent in tag.parents if getattr(parent, "attrs", None))):
            continue
        if tag.name == "a" and (not tag.get("href") or
                                tag.get("href", "").lower().startswith("javascript:")):
            continue
        return True
    return False


def _header_location(lines: list[str], title_index: int, role_index: int) -> str:
    """Retain visible metadata, including restrictions on separate lines.

    Remove only exact known labels; don't turn Remote into worldwide eligibility
    or erase a country that might appear next to it on a future version.
    """
    ignored = {"hylo", "marketing & communications", "contractor", "apply now", "·", "|",
               "marketing & communications · contractor"}
    values = []
    for line in lines[title_index + 1:role_index]:
        if (line.casefold() in ignored or line.casefold() == "posted" or
                re.match(r"^Posted on\b", line, re.I) or
                re.fullmatch(r"on [A-Za-z]+ \d{1,2}, \d{4}", line, re.I)):
            continue
        if line not in values:
            values.append(line)
    return "; ".join(values)


def fetch_hylo() -> list[boards.Job]:
    """Read one exact public posting through the shared HTTP bounds.

    Fail closed for redirects, errors, missing body/identity, closure or inactive
    Apply CTA. Visible structural headings are used instead of guessed Getro
    CSS classes. Full visible role text survives for downstream restriction checks.
    """
    try:
        response = boards._get(HYLO_JOB_URL, follow_redirects=True, timeout=12)
        response.raise_for_status()
        if not _same_company_page(str(response.url), HYLO_JOB_URL):
            return []
        soup = _visible_soup(response.text)
        heading = next((tag for tag in soup.select("h1, h2, h3")
                        if _normal(tag.get_text(" ", strip=True)) == HYLO_TITLE), None)
        if heading is None:
            return []
        for tag in soup.select("nav, footer"):
            tag.decompose()
        body = soup.body or soup
        if not _active_apply(body):
            return []
        description = body.get_text(" ", strip=True)
        if _CLOSED.search(description):
            return []
        # These independently verified identity markers distinguish the crypto
        # employer from unrelated organizations with the same short name.
        if not all(re.search(r"\b" + re.escape(token) + r"\b", description, re.I)
                   for token in ("Hylo", "Solana", "xSOL", "hyUSD")):
            return []
        sections = {_normal(tag.get_text(" ", strip=True)).casefold().replace("’", "'")
                    for tag in body.select("h1, h2, h3, h4, h5, h6")}
        if not {"the role", "about hylo", "what you'll do", "what you bring"} <= sections:
            return []
        if len(description.split()) < 70:
            return []
        # Inline spans can split the title. Use a tag boundary to get metadata
        # without assuming one text node per heading.
        heading.string = HYLO_TITLE
        lines = [_normal(line) for line in body.get_text("\n", strip=True).splitlines()
                 if _normal(line)]
        title_index = lines.index(HYLO_TITLE)
        role_index = next(i for i in range(title_index + 1, len(lines))
                          if lines[i].casefold() == "the role")
        location = _header_location(lines, title_index, role_index)
        posted = _board_date(description)
        return [boards.Job(
            id=boards._make_id(HYLO_JOB_URL), title=HYLO_TITLE, company="Hylo",
            location=location, url=HYLO_JOB_URL, source="ecosystem/solana/hylo",
            posted=posted, description=description,
            published_at_upper_bound=HYLO_EXISTED_BY,
            current_open_verified=True,
        )]
    except Exception as exc:
        print(f"[ecosystem/solana/hylo] ERROR: {exc}")
        return []

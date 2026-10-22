"""Exact roles individually requested after human research on 22 October 2026.

This is a bounded reviewed shortlist, not authenticated social discovery and
not a generic filter override. Re-fetch the public listing each run; retain the
first observed posting dates and all older history. No cookies or credentials.
"""
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import boards

ZONIQX = 'https://www.linkedin.com/jobs/view/4479493250/'
BINANCE = 'https://job-boards.eu.greenhouse.io/binance/jobs/5002385101'


def fetch_reviewed_roles():
    response = boards._get(ZONIQX, follow_redirects=True, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.select_one('h1')
    description = soup.select_one('.show-more-less-html__markup')
    company = soup.select_one('.topcard__org-name-link')
    text = soup.get_text(' ', strip=True)
    if (not title or title.get_text(' ', strip=True) != 'Marketing Manager' or
        not company or 'Zoniqx' not in company.get_text() or not description or
        'no longer accepting applications' in text.lower() or
        not soup.select_one('.apply-button')):
        from source_health import record_failure
        record_failure()
        return []
    desc = description.get_text(' ', strip=True)
    if 'India (Remote)' not in desc or 'Own and run' not in desc:
        return []
    return [boards.Job(boards._make_id(ZONIQX), 'Marketing Manager', 'Zoniqx',
        'India (Remote)', ZONIQX, 'reviewed/zoniqx', posted='2026-10-22',
        description=desc, current_open_verified=True)]


def reviewed_details(job, now=None):
    """Narrow editorial inclusion; never call unresolved employer evidence qualified."""
    from filters import _is_too_old, _parse_posted_date
    now = now or datetime.now(timezone.utc)
    if not job.current_open_verified or _is_too_old(job, now):
        return None
    date = _parse_posted_date(job.posted, now)
    if not date or date > now:
        return None
    if now.date().isoformat() > '2026-11-20':
        return None
    text = job.description.lower()
    if (job.url.rstrip('/') == ZONIQX.rstrip('/') and job.company == 'Zoniqx' and
        job.title == 'Marketing Manager' and job.location == 'India (Remote)' and
        all(x in text for x in ('own and run', 'marketing across channels', '2+ years', 'india (remote)'))):
        return {'adjacent': False, 'note': '2+ years · Finance/digital-assets writing'}
    if (job.url == BINANCE and job.company == 'Binance' and
        job.title == 'Product Marketing Manager - South Asia' and job.location == 'India' and
        all(x in text for x in ('5+ years', '3 years', 'go-to-market', 'remote-first'))):
        return {'adjacent': True,
                'note': '5+ years · 3+ in Web3 · Confirm team remote terms and employing entity',
                'display_location': 'India · Remote advertised on LinkedIn'}
    return None

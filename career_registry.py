"""Reviewed expansion from a pinned, data-only public company registry.

Unreviewed rows never cause network requests or establish employer facts.
Existing curated boards always win. The upstream crawler is never executed.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import boards
from source_health import record_warning

REGISTRY_PATH = Path(__file__).with_name('career_registry.json')
MAX_ADDITIONAL_BOARDS = 12
MAX_ASSOCIATION_AGE_DAYS = 90
_HOSTS = {
    'jobs.ashbyhq.com': 'ashby',
    'jobs.lever.co': 'lever',
    'boards.greenhouse.io': 'greenhouse',
    'boards.eu.greenhouse.io': 'greenhouse',
    'job-boards.greenhouse.io': 'greenhouse',
    'job-boards.eu.greenhouse.io': 'greenhouse',
}


def board_key(url):
    """Allow only supported public ATS board URLs, never arbitrary endpoints."""
    if not isinstance(url, str) or re.search(r'[\x00-\x20\x7f\\]', url):
        return None
    try:
        p = urlsplit(url)
        slug = p.path.strip('/')
        if (p.scheme != 'https' or p.hostname not in _HOSTS or p.username or p.password
                or p.port not in (None, 443) or p.query or p.fragment
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', slug)):
            return None
        return _HOSTS[p.hostname], slug
    except ValueError:
        return None


def load_registry(path=REGISTRY_PATH):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('schema_version') != 1 or not isinstance(data.get('companies'), list)
            or len(data['companies']) > 500):
        raise ValueError('Invalid career discovery registry')
    if any(not isinstance(row, dict) for row in data['companies']):
        raise ValueError('Invalid career discovery record')
    return data


def curated_keys():
    from direct_sources import ASHBY_COMPANIES
    return ({('ashby', slug) for slug, _ in ASHBY_COMPANIES}
            | {('lever', slug) for slug, _ in boards.LEVER_COMPANIES}
            | {('greenhouse', slug) for slug, _ in boards.GREENHOUSE_COMPANIES})


def reviewed(row, now):
    review = row.get('review')
    if not isinstance(review, dict) or review.get('status') != 'verified_association':
        return False
    # Approval is bound to this exact board URL, not an upstream company's name.
    if review.get('careers_url') != row.get('careers_url'):
        return False
    try:
        checked = date.fromisoformat(review.get('checked_at', ''))
        age = (now.date() - checked).days
        company = urlsplit(row.get('company_url', ''))
        evidence = urlsplit(review.get('official_source_url', ''))
        host = lambda p: (p.hostname or '').lower().removeprefix('www.')
        return (0 <= age <= MAX_ASSOCIATION_AGE_DAYS
                and company.scheme == evidence.scheme == 'https'
                and bool(host(company)) and host(company) == host(evidence)
                and evidence.port in (None, 443) and not evidence.username and not evidence.password
                and not evidence.query and not evidence.fragment
                and isinstance(review.get('company_name'), str) and bool(review['company_name'].strip()))
    except (ValueError, TypeError):
        return False


def registry_audit(data=None, now=None):
    data = data if data is not None else load_registry()
    now = now or datetime.now(timezone.utc)
    existing = curated_keys()
    counts = Counter()
    selected, seen = [], set()
    for row in data['companies']:
        key = board_key(row.get('careers_url'))
        if key in existing:
            counts['already_curated'] += 1
        elif key is None:
            counts['unsupported_or_non_board_url'] += 1
        elif key in seen:
            counts['duplicate_board'] += 1
        elif not reviewed(row, now):
            counts['requires_association_review'] += 1
        else:
            seen.add(key)
            selected.append({'adapter': key[0], 'slug': key[1],
                             'company': row['review']['company_name'],
                             'careers_url': row['careers_url'],
                             'official_source_url': row['review']['official_source_url'],
                             'checked_at': row['review']['checked_at']})
    counts['reviewed_additional'] = len(selected)
    counts['active_additional'] = min(len(selected), MAX_ADDITIONAL_BOARDS)
    return {'total_discovery_companies': len(data['companies']), 'counts': dict(counts),
            'active': selected[:MAX_ADDITIONAL_BOARDS],
            'bounded': len(selected) > MAX_ADDITIONAL_BOARDS,
            'notice': 'Discovery URLs are not employer-HQ, eligibility, original-age or job-opening evidence.'}


def fetch_registry_careers():
    from direct_sources import _fetch_ashby_one
    audit = registry_audit()
    if audit['bounded']:
        record_warning('bounded_registry_boards')
    if audit['counts'].get('requires_association_review'):
        record_warning('registry_unreviewed_associations_not_fetched')
    adapters = {'ashby': _fetch_ashby_one, 'lever': boards._fetch_lever_one,
                'greenhouse': boards._fetch_greenhouse_one}
    jobs = []
    for row in audit['active']:
        jobs.extend(adapters[row['adapter']](row['slug'], row['company']))
    return jobs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Audit passive career discovery and reviewed adapter coverage; no network calls.')
    parser.add_argument('--audit', action='store_true', required=True)
    parser.parse_args()
    print(json.dumps(registry_audit(), indent=2))

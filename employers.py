"""Attach independently checked employer-base evidence, never infer from job location."""
import json
import re
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name('employers.json')


def _key(name: str) -> str:
    return re.sub(r'\s+', ' ', name or '').strip().casefold()


@lru_cache(maxsize=1)
def _registry() -> dict:
    data = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    aliases = {}
    for employer in data['employers']:
        for name in employer['aliases']:
            key = _key(name)
            if key in aliases and aliases[key] != employer:
                raise ValueError('Ambiguous employer alias in registry')
            aliases[key] = employer
    return aliases


def enrich_employers(jobs: list) -> list:
    """Use exact aliases only. Filters check evidence age and India exclusions."""
    registry = _registry()
    for job in jobs:
        record = registry.get(_key(job.company), {})
        if record.get('verification_status') == 'verified':
            job.employer_country = record.get('base_country_code') or ''
            job.employer_country_source = record.get('official_source_url') or ''
            job.employer_country_verified_at = record.get('checked_at') or ''
        else:
            job.employer_country = ''
            job.employer_country_source = ''
            job.employer_country_verified_at = ''
    return jobs

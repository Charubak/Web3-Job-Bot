"""Public ecosystem index discovery; results are leads, never verified openings.

Paradigm's careers index and Solana's official ecosystem directory replace dead
assumed Greenhouse slugs. The company associated with an ecosystem link is not
the index operator; preserve unknown identity for the research step.
"""
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
import boards
from source_health import record_failure, record_warning

INDEXES = (
    ('Paradigm ecosystem', 'https://www.paradigm.xyz/careers'),
    ('Solana ecosystem', 'https://jobs.solana.com/jobs'),
)
MAX_LEADS_PER_INDEX = 60


def fetch_career_indexes():
    jobs = []
    seen = set()
    for label, url in INDEXES:
        try:
            response = boards._get(url, follow_redirects=True, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            if soup.select_one("#challenge-running, #b_captcha, form[action*='captcha']"):
                raise ValueError('Public careers index challenged')
            found = 0
            for anchor in soup.select('a[href]'):
                target = urljoin(str(response.url), anchor['href'])
                parsed = urlsplit(target)
                if parsed.scheme != 'https' or parsed.username or parsed.password:
                    continue
                if parsed.hostname not in {'jobs.ashbyhq.com', 'jobs.lever.co', 'job-boards.greenhouse.io', 'job-boards.eu.greenhouse.io', 'jobs.solana.com'}:
                    continue
                if not any(marker in parsed.path for marker in ('/jobs/', '/job/', '/roles/')) and parsed.hostname not in {'jobs.ashbyhq.com', 'jobs.lever.co'}:
                    continue
                text = anchor.get_text(' ', strip=True)
                if not any(term in text.casefold() for term in ('marketing', 'growth', 'go-to-market', 'gtm')):
                    continue
                canonical = boards.canonical_job_url(target)
                if canonical in seen:
                    continue
                seen.add(canonical)
                title_node = anchor.select_one('h2, h3, h4, [itemprop="title"]')
                title = title_node.get_text(' ', strip=True) if title_node else text
                jobs.append(boards.Job(boards._make_id(canonical), title[:200], '', '', canonical,
                    'careers/index', description=f'Linked from {label}: {url}\n{text[:3000]}'))
                found += 1
                if found >= MAX_LEADS_PER_INDEX:
                    record_warning('bounded_career_index')
                    break
        except Exception as exc:
            record_failure()
            print(f'[careers/index] {label} unavailable ({type(exc).__name__})')
    record_warning('index_leads_require_primary_verification')
    return jobs

"""No-message public evidence export for a separate research/delivery worker.

This module never imports Telegram configuration/notifiers, changes delivery
history, or labels a newly collected row as a newly delivered job. Publication
is an explicit allowlist. SQLite seed reads job-history columns only, read-only.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

from boards import fetch_all, canonical_job_url
from employers import enrich_employers, _registry, _key
from filters import FilterDecision, classify_job, _parse_posted_date, _is_primary_source, MAX_AGE_DAYS
from source_health import snapshot

SCHEMA_VERSION = 1


# Explicit names only: an unknown parameter may be a requisition selector or a
# secret. Dropping it can silently point an Apply link at a different opening.
_PUBLIC_ID_KEYS = {
    'id', 'jobid', 'job_id', 'gh_jid', 'postingid', 'posting_id',
    'positionid', 'position_id', 'requisitionid', 'requisition_id',
    'reqid', 'req_id', 'vacancyid', 'vacancy_id', 'job', 'jobnumber',
    'job_number', 'requisition', 'requisitionnumber', 'requisition_number',
}
_PRIVATE_QUERY_KEYS = {
    'token', 'access_token', 'refresh_token', 'auth', 'authorization',
    'auth_token', 'api_key', 'apikey', 'key', 'secret', 'password', 'code',
    'session', 'sessionid', 'session_id', 'email', 'email_address', 'phone',
    'contact', 'name', 'first_name', 'last_name', 'signature', 'sig',
}
_TRACKING_KEYS = {
    'gclid', 'dclid', 'fbclid', 'msclkid', 'mc_cid', 'mc_eid',
    'gh_src', 'lever-source', 'lever-origin',
}
_PUBLIC_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def _public_query(query):
    """Return safe selectors without rewriting their ordering or encoding."""
    kept = []
    for item in query.split('&'):
        if not item:
            continue
        key, sep, value = item.partition('=')
        key = unquote_plus(key).casefold()
        if key in _PRIVATE_QUERY_KEYS or key in _TRACKING_KEYS or key.startswith(('utm_', 'x-amz-', 'x-goog-')):
            continue
        if key not in _PUBLIC_ID_KEYS or not sep or not _PUBLIC_ID.fullmatch(unquote_plus(value)):
            return None
        kept.append(item)
    return '&'.join(kept)


def _public_fragment(fragment):
    if not fragment:
        return ''
    # Hash-router paths and explicit requisition anchors are public identity.
    route, sep, query = fragment.partition('?')
    if re.fullmatch(r'!?/?(?:jobs?|postings?|positions?|vacanc(?:y|ies)|requisitions?)/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/?(?:apply/?)?', route, re.I):
        clean = _public_query(query)
        return None if clean is None else route + ('?' + clean if clean else '')
    if re.fullmatch(r'(?:job|posting|position|vacancy|requisition)[-_:][A-Za-z0-9][A-Za-z0-9_.-]{0,127}', fragment, re.I):
        return fragment
    if '=' in fragment and '/' not in fragment:
        return _public_query(fragment)
    if '/' in fragment or '=' in fragment or sep:
        return None
    # Plain document anchors do not distinguish openings.
    return ''


def public_url(value):
    """Public links retaining supported job identity; withhold ambiguous links."""
    try:
        raw = str(value or '')
        if re.search(r'[\x00-\x20\x7f\\]', raw):
            return ''
        p = urlsplit(raw)
        host = (p.hostname or '').lower().rstrip('.')
        if p.scheme not in {'https', 'http'} or p.username or p.password or not host:
            return ''
        if host in {'localhost', 'metadata.google.internal'} or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home')) or '.' not in host and ':' not in host:
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            # Reject alternate numeric/encoded host spellings instead of relying
            # on browser-specific localhost/address interpretation.
            if '%' in host or re.fullmatch(r'(?:0x[0-9a-f]+|[0-9.]+)', host):
                return ''
        p.port  # Validate malformed ports before publishing.
        query = _public_query(p.query)
        fragment = _public_fragment(p.fragment)
        if query is None or fragment is None or '@' in unquote(p.path):
            return ''
        return urlunsplit((p.scheme, p.netloc, p.path, query, fragment))
    except ValueError:
        return ''


def public_text(value, limit=2400):
    text = str(value or '')
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[public contact omitted]', text)
    text = re.sub(r'https?://\S+', lambda m: public_url(m.group(0)), text)
    return text[:limit]


def identity(job):
    return hashlib.sha256(canonical_job_url(job.url).encode()).hexdigest()[:16]


def merge_history(*histories, now=None):
    """Union durable evidence for exact identities; never overwrite tombstones."""
    now = now or datetime.now(timezone.utc)
    result = {}
    for history in histories:
        for key, incoming in history.items():
            old = result.get(key, {})
            row = {**old, **incoming}
            for field in ('first_seen_at', 'first_posted_at', 'oldest_existence_at', 'expired_at', 'last_seen_at'):
                values = [r.get(field) for r in (old, incoming) if r.get(field)]
                parsed = [(_parse_posted_date(v, now=now), v) for v in values]
                valid = [(d, v) for d, v in parsed if d is not None]
                if valid:
                    choose = max if field == 'last_seen_at' else min
                    row[field] = choose(valid, key=lambda item: item[0])[1]
                elif values:
                    row[field] = values[0]
            result[key] = row
    return result


def seed_history(path):
    """Do not read activation, recipient, Telegram messages or delivery tables."""
    if not path or not Path(path).is_file():
        return {}
    history = {}
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_history'").fetchone():
            return history
        for url, first_seen, posted, expired in conn.execute('SELECT url, first_seen_at, first_posted_at, expired_at FROM job_history'):
            clean = public_url(url)
            # Withheld public links still retain their hashed age evidence.
            if url:
                key = hashlib.sha256(canonical_job_url(url).encode()).hexdigest()[:16]
                history[key] = merge_history({key: history.get(key, {})},
                    {key: {'url': clean, 'first_seen_at': first_seen,
                           'first_posted_at': posted, 'expired_at': expired,
                           'last_seen_at': first_seen}})[key]
    return history


def reconcile(jobs, previous, now):
    """Same requisition only: retain original/oldest dates and permanent expiry."""
    history = {key: dict(row) for key, row in previous.items()}
    updated = []
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    for job in jobs:
        key = identity(job)
        old = history.get(key, {})
        first_seen = _parse_posted_date(old.get('first_seen_at'), now=now) or now
        current_posted = _parse_posted_date(job.posted, now=now)
        dates = [_parse_posted_date(old.get('first_posted_at'), now=now), current_posted]
        dates = [d for d in dates if d is not None and d <= now]
        posted = min(dates) if dates else None
        bounds = [_parse_posted_date(value, now=now) for value in (old.get('oldest_existence_at'), job.published_at_upper_bound)]
        bounds = [d for d in bounds if d is not None and d <= now]
        existence = min([first_seen] + bounds + dates)
        expired = old.get('expired_at') or (now.isoformat() if existence < cutoff else None)
        history[key] = {'url': public_url(job.url), 'first_seen_at': first_seen.isoformat(),
                        'first_posted_at': posted.isoformat() if posted else old.get('first_posted_at'),
                        'oldest_existence_at': existence.isoformat(),
                        'expired_at': expired, 'last_seen_at': now.isoformat()}
        # Unknown/future dates are not invented from first observation.
        listing = replace(job)
        if expired:
            if existence < cutoff:
                listing.posted = existence.isoformat()
        elif posted and current_posted is not None and current_posted <= now:
            listing.posted = posted.isoformat()
        updated.append(listing)
    return updated, history


def related_key(job):
    employer = _registry().get(_key(job.company), {})
    name = employer.get('employer_name') or job.company
    title = re.sub(r'[^a-z0-9]+', ' ', job.title.casefold()).strip()
    return (_key(name), title) if name else ('', job.id)


def research_tasks(job, decision):
    reason = decision.reason.casefold()
    tasks = []
    checks = (
        ('employer', 'Verify employer identity and explicit non-India headquarters/base from an authoritative source.'),
        ('first-party', 'Follow the listing to the official employer or ATS, and join evidence only for this exact requisition.'),
        ('description', 'Read the full current role description; distinguish broad ownership from dedicated community/BD/events/social duties.'),
        ('scope', 'Check responsibility and seniority from the full role, not just the title.'),
        ('location', 'Verify India eligibility and distinguish market scope, working hours, benefits and applicant residence.'),
        ('remote', 'Check role-level remote terms and all contradictory residence/office requirements.'),
        ('jurisdiction', 'Resolve exact jurisdiction exclusions and structured ATS country metadata; do not infer US-only hiring from US-only benefits.'),
        ('posting date', 'Find the earliest original posting date; label unknown age without inventing a date.'),
    )
    for needle, action in checks:
        if needle in reason:
            tasks.append(action)
    if not job.current_open_verified:
        tasks.append('Verify that this exact opening is still active; recent unconfirmed-open evidence must retain that label.')
    if job.ats_country and job.ats_country.casefold() not in {'in', 'india'}:
        tasks.append(f'ATS country field is {public_text(job.ats_country, 60)}; reconcile it with Remote terms before claiming India eligibility.')
    return list(dict.fromkeys(tasks))


def public_candidate(job, decision, previous, now):
    posted = _parse_posted_date(job.posted, now=now)
    if posted is None or posted > now:
        age = 'unknown'
    elif posted < now - timedelta(days=MAX_AGE_DAYS):
        age = 'older_than_30_days'
    else:
        age = 'within_30_days'
    return {
        'id': identity(job), 'title': public_text(job.title, 250),
        'company': public_text(job.company, 150), 'location': public_text(job.location, 300),
        'url': public_url(job.url), 'source': public_text(job.source, 100),
        'posted': job.posted, 'published_at_upper_bound': job.published_at_upper_bound,
        'salary': public_text(job.salary, 250), 'ats_country': public_text(job.ats_country, 80),
        'description_excerpt': public_text(job.description),
        'primary_source': _is_primary_source(job),
        'employer_base': {'country': job.employer_country,
                          'source': public_url(job.employer_country_source),
                          'checked_at': job.employer_country_verified_at},
        'classification': decision.status, 'reasons': list(decision.reasons),
        'timezone_note': public_text(decision.timezone_note, 700),
        'age_status': age,
        'opening_status': 'verified_open' if job.current_open_verified else 'unconfirmed_open',
        'collection_status': 'previously_collected' if identity(job) in previous else 'first_collection',
        'research_tasks': research_tasks(job, decision),
    }


def build_report(jobs, previous, sources, now=None):
    now = now or datetime.now(timezone.utc)
    reconciled, history = reconcile(enrich_employers(jobs), previous, now)
    decisions = []
    for job in reconciled:
        decision = classify_job(job, now=now)
        if history[identity(job)].get('expired_at') and decision.status != 'rejected':
            decision = FilterDecision(job, 'rejected',
                ('This exact requisition was previously expired; retained history excludes it',),
                decision.timezone_note)
        decisions.append((job, decision))
    counts = Counter(d.status for _, d in decisions)
    rejection_counts = Counter(d.reason for _, d in decisions if d.status == 'rejected')
    review_counts = Counter(reason for _, d in decisions if d.status == 'review' for reason in d.reasons)
    withheld_urls = sum(d.status != 'rejected' and not public_url(j.url) for j, d in decisions)
    candidates = [public_candidate(j, d, previous, now) for j, d in decisions if d.status != 'rejected' and public_url(j.url)]
    # Keep distinct requisitions. Related evidence is a research hint, never
    # permission to copy dates/eligibility between same-title openings.
    groups = defaultdict(list)
    for j, d in decisions:
        if public_url(j.url):
            groups[related_key(j)].append({'url': public_url(j.url), 'source': j.source,
                'posted': j.posted, 'primary_source': _is_primary_source(j), 'classification': d.status})
    by_id = {identity(j): j for j in reconciled}
    for row in candidates:
        row['related_evidence'] = [r for r in groups[related_key(by_id[row['id']])] if r['url'] != row['url']]
    # Relevant hard negatives remain auditable, but are not proposed as fits.
    rejected = [public_candidate(j, d, previous, now) for j, d in decisions
                if d.status == 'rejected' and re.search(r'\b(marketing|growth|gtm)\b', j.title, re.I) and public_url(j.url)]
    source_rows = []
    for source in sources:
        source_rows.append({
            'source': source['source'], 'status': source['status'], 'count': source['count'],
            'failures': source.get('failures', 0), 'warning': source.get('warning', ''),
            'warnings': source.get('warnings', []),
            'requests': [{'url': public_url(r['url']), 'status_code': r['status_code'],
                          'elapsed_seconds': r['elapsed_seconds']} for r in source.get('requests', [])],
        })
    failed = [s['source'] for s in source_rows if s['status'] != 'returned']
    zeros = [s['source'] for s in source_rows if not s['count']]
    successful_zeros = [s['source'] for s in source_rows if not s['count'] and s['status'] == 'returned']
    bounded = [s['source'] for s in source_rows
               if any(w.startswith('bounded_') for w in s['warnings'])]
    report = {
        'schema_version': SCHEMA_VERSION, 'generated_at': now.isoformat(),
        'collector_commit': os.environ.get('GITHUB_SHA', ''),
        'run_url': ('https://github.com/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+os.environ['GITHUB_RUN_ID'])
                   if os.environ.get('GITHUB_REPOSITORY') and os.environ.get('GITHUB_RUN_ID') else '',
        'collection_only': True,
        'notice': 'Public collection evidence, not a delivered digest. First collection does not mean a new job. Research unresolved evidence and use private delivery history to label new/reminders.',
        'counts': {'raw': len(jobs), 'qualified': counts['qualified'], 'review': counts['review'],
                   'rejected': counts['rejected'], 'unconfirmed_open': sum(r['opening_status'] == 'unconfirmed_open' for r in candidates),
                   'unknown_age': sum(r['age_status'] == 'unknown' for r in candidates),
                   'source_failures': len(failed), 'zero_result_sources': len(zeros),
                   'withheld_public_urls': withheld_urls},
        'coverage': {'complete': False, 'failed_or_partial_sources': failed, 'zero_result_sources': zeros,
                     'successful_zero_result_sources': successful_zeros, 'bounded_sources': bounded,
                     'withheld_public_urls': withheld_urls,
                     'url_warning': (f'{withheld_urls} non-rejected listings were withheld because their public application URL could not be safely preserved. Verify the exact requisitions before proposing them.' if withheld_urls else ''),
                     'limitations': ['Social adapters use bounded public Bing indexes, not native X/LinkedIn search.',
                                    'Board pagination and company/index lists are bounded.',
                                    'Unknown employer, remote eligibility, original age and active status require research.']},
        'sources': source_rows,
        'rejection_counts': dict(rejection_counts.most_common()),
        'review_reason_counts': dict(review_counts.most_common()),
        'candidates': candidates, 'rejected_candidates': rejected,
        'research_queue': [{'id': r['id'], 'title': r['title'], 'company': r['company'],
                            'url': r['url'], 'reasons': r['reasons'], 'tasks': r['research_tasks']}
                           for r in candidates if r['classification'] == 'review' or r['research_tasks']],
        'search_followups': [
            {'source': 'X', 'query': '(web3 OR crypto OR defi) (hiring OR "join us") ("marketing manager" OR "growth lead" OR "head of marketing")', 'required': True},
            {'source': 'LinkedIn posts/jobs', 'query': '(web3 OR crypto OR defi) ("marketing manager" OR "growth strategist" OR "head of growth"); search variants both with and without remote.', 'required': True},
            {'source': 'CryptoJobsList', 'query': 'site:cryptojobslist.com/jobs (marketing OR growth) remote', 'required': True},
            {'source': 'Blocked or partial sources', 'query': 'Use independent browsing for every failed_or_partial_sources entry. Attempt the source directly, record access failure if blocked, then search its indexed roles and official company careers. Never report access failure as zero jobs.', 'required': True},
            {'source': 'Bounded sources', 'query': 'Inspect coverage.bounded_sources and each source warnings list. If a permitted native search hits its cap before the date cutoff, narrow by title family or supported date window, deduplicate exact posts and report any remaining truncation. A capped feed is not complete coverage. Stop at access restrictions.', 'required': True},
            {'source': 'Company careers', 'query': 'Follow primary links for all promising unresolved candidates and newly discovered employers.', 'required': True},
        ],
    }
    return report, {'schema_version': SCHEMA_VERSION, 'updated_at': now.isoformat(), 'jobs': history}


def markdown(report):
    lines = ['# Public job collection', '', 'Generated: '+report['generated_at'], '', report['notice'], '',
             'Counts: '+json.dumps(report['counts'], sort_keys=True), '',
             'Incomplete sources: '+(', '.join(report['coverage']['failed_or_partial_sources']) or 'none reported'),
             'Successful requests with no listings (not proof of no jobs): '+(', '.join(report['coverage'].get('successful_zero_result_sources', [])) or 'none'),
             'Bounded sources: '+(', '.join(report['coverage'].get('bounded_sources', [])) or 'none reported'), '',
             'Public application URL warning: '+(report['coverage'].get('url_warning') or 'none'), '',
             '## Candidates and unresolved evidence', '']
    for row in report['candidates']:
        title = row['title'].replace('\n', ' ').replace('[', '').replace(']', '')
        lines.extend([f"### {row['company']} | {title}", '', row['url'],
                      f"{row['classification']} | {row['opening_status']} | {row['age_status']} | {row['collection_status']}",
                      'Location: '+row['location'], 'Reasons: '+'; '.join(row['reasons']),
                      'Research: '+' '.join(row['research_tasks']), ''])
    lines.extend(['## Source health', ''])
    for source in report['sources']:
        warnings = list(dict.fromkeys(filter(None, [source.get('warning', ''), *source.get('warnings', [])])))
        detail = '; warnings: ' + ', '.join(warnings) if warnings else ''
        lines.append(f"- {source['source']}: {source['status']}, {source['count']} listings, {source['failures']} failures, {len(source['requests'])} requests{detail}")
    lines.extend(['', '## Required independent follow-ups', ''])
    lines.extend(f"- {s['source']}: {s['query']}" for s in report['search_followups'])
    return '\n'.join(lines)+'\n'


def collect(output, history_path=None, legacy_history=None):
    output = Path(output)
    previous = seed_history(legacy_history)
    if history_path and Path(history_path).is_file():
        previous = merge_history(previous, json.loads(Path(history_path).read_text())['jobs'])
    jobs = fetch_all()
    if not jobs:
        raise RuntimeError('All collection sources returned empty; do not overwrite latest successful evidence')
    report, history = build_report(jobs, previous, snapshot())
    output.mkdir(parents=True, exist_ok=True)
    # Keep every connector fetch below200KiB, even as the queue grows. The
    # manifest is compact; full evidence is in explicitly listed JSON shards.
    manifest = {k: v for k, v in report.items() if k not in {'candidates', 'rejected_candidates', 'research_queue'}}
    manifest['candidate_files'] = write_shards(output, 'candidates', report['candidates'])
    manifest['rejection_files'] = write_shards(output, 'rejections', report['rejected_candidates'])
    manifest['research_queue_files'] = write_shards(output, 'research-queue', report['research_queue'])
    (output/'latest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n')
    (output/'latest.md').write_text(markdown(report))
    (output/'history.json').write_text(json.dumps(history, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'counts': report['counts'], 'generated_at': report['generated_at'],
                      'failed_sources': report['coverage']['failed_or_partial_sources']}))
    return report


def write_shards(output, folder, rows, limit=190_000):
    directory = output/folder
    directory.mkdir(exist_ok=True)
    for stale in directory.glob('part-*.json'):
        stale.unlink()
    files, batch, size = [], [], 2
    def flush():
        name = f'{folder}/part-{len(files)+1:03d}.json'
        (output/name).write_text(json.dumps(batch, ensure_ascii=False, separators=(',', ':'))+'\n')
        files.append(name)
    for row in rows:
        encoded = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))+1
        if encoded > limit-2:
            raise ValueError('One public evidence row exceeds the connector bound')
        if batch and size+encoded > limit:
            flush()
            batch, size = [], 2
        batch.append(row)
        size += encoded
    if batch or not files:
        flush()
    return files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--history')
    parser.add_argument('--legacy-history')
    args = parser.parse_args()
    collect(args.output, args.history, args.legacy_history)


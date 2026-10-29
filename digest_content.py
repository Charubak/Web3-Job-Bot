"""Application-first cards; rendering never changes delivery history."""
import html
import re
from boards import Job
from filters import _parse_posted_date

def card(entry, number):
    job = Job(**entry['job'])
    date = _parse_posted_date(job.posted)
    age = f'Posted {date:%d %b}' if date else 'Posting date unknown'
    location = entry.get('display_location') or job.location or 'Location details on application page'
    if job.company.lower() == 'getblock' and 'most of the team works around utc+3' in job.description.lower():
        location += ' · UTC+3 overlap required'
    tag = 'NEW' if entry['label'] == 'new' else 'REMINDER'
    lines = [f'📌 <b>{html.escape(job.company)} | {html.escape(job.title)}</b>',
             f'{tag} · {age}', f'🌍 {html.escape(location)}']
    # Salary is the source-provided field, never an inferred estimate.
    if job.salary and job.salary.strip():
        lines.append(f'💰 {html.escape(job.salary.strip())}')
    if entry.get('note'):
        lines.append(html.escape(entry['note']))
    lines.append(f'🔗 <a href="{html.escape(job.url, quote=True)}">Apply</a>')
    return '\n'.join(lines)


def coverage_note(report):
    sources = report.get('sources', [])
    if not report.get('counts', {}).get('raw', 1):
        return 'Search coverage was unavailable or empty today; this does not mean there are no suitable jobs.'
    if not sources or any(s['status'] != 'returned' for s in sources):
        failed = [s.get('source', '') for s in sources if s['status'] != 'returned' and s.get('source')]
        detail = ('Incomplete checks: ' + ', '.join(failed) + '.\n') if failed else ''
        return detail + 'Some job sources could not be fully checked today, so this list may be incomplete.'
    if any(not s['count'] for s in sources):
        return 'Some sources returned no listings; search coverage may be incomplete.'
    return ''


def is_adjacent(entry):
    title = entry['job']['title'].lower()
    # Broad, multi-channel roles remain primary even when PMM is part of the title.
    return entry.get('adjacent', False) or bool(re.search(r'\b(product marketing|pmm)\b', title)) and not bool(re.search(r'\b(growth|full[ -]stack)\b', title))


def messages(report, summary):
    entries = report.get('digest_jobs', [])
    adjacent = [e for e in entries if e.get('adjacent')]
    dated = [e for e in entries if not e.get('adjacent') and _parse_posted_date(e['job']['posted'])]
    unknown = [e for e in entries if not e.get('adjacent') and not _parse_posted_date(e['job']['posted'])]
    dated.sort(key=lambda e: _parse_posted_date(e['job']['posted']), reverse=True)
    groups = [('New openings', [e for e in dated if e['label'] == 'new']),
              ('Reminders', [e for e in dated if e['label'] == 'previously_shared']),
              ('New openings | Date unknown', [e for e in unknown if e['label'] == 'new']),
              ('Reminders | Date unknown', [e for e in unknown if e['label'] == 'previously_shared'])]
    # Keep newest first within each fit tier; never relax qualification.
    for _, items in groups:
        items.sort(key=is_adjacent)
    groups.append(('Adjacent option', adjacent))
    text = summary
    if not entries:
        text += '\nNo previously shared roles could be revalidated for this digest.'
    chunks, new_jobs, number = [], [], 0
    for heading, items in groups:
        for index, entry in enumerate(items):
            number += 1
            prefix = f'<b>{heading}</b>\n' if index == 0 else ''
            if index == 0 and not _parse_posted_date(entry['job']['posted']):
                prefix += '<i>Still open; not verified within 30 days.</i>\n'
            block = prefix + card(entry, number)
            if len(text) + len(block) + 2 > 3800:
                chunks.append({'text': text, 'new_jobs': new_jobs})
                text, new_jobs = '<b>Jobs to consider | Continued</b>', []
                if not prefix:
                    block = f'<b>{heading}</b>\n' + block
            if len(block) > 3500:
                raise ValueError('Job card exceeds safe message length')
            text += '\n\n' + block
            if entry['label'] == 'new':
                new_jobs.append(entry['job'])
    note = coverage_note(report)
    if note:
        if len(text) + len(note) + 2 > 3800:
            chunks.append({'text': text, 'new_jobs': new_jobs})
            text, new_jobs = '', []
        text += ('\n\n' if text else '') + note
    chunks.append({'text': text, 'new_jobs': new_jobs})
    return chunks

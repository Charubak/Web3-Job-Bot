"""Render fresh, actively revalidated jobs, retaining explicit reminder labels."""
from datetime import datetime, timezone
from boards import Job
from filters import _parse_posted_date
from notifier import _format_job


def messages(report, summary):
    entries = report.get('digest_jobs', [])
    dated = [e for e in entries if _parse_posted_date(e['job']['posted'])]
    unknown = [e for e in entries if not _parse_posted_date(e['job']['posted'])]
    dated.sort(key=lambda e: _parse_posted_date(e['job']['posted']), reverse=True)
    groups = [('New — posted within 30 days', [e for e in dated if e['label'] == 'new']),
              ('Previously shared — still-open reminders within 30 days', [e for e in dated if e['label'] == 'previously_shared']),
              ('Active — original posting date unknown (not verified within 30 days)', unknown)]
    chunks = []
    text = summary
    new_jobs = []
    for heading, items in groups:
        for index, entry in enumerate(items):
            prefix = f'<b>{heading}</b>\n' if index == 0 else ''
            label = 'New' if entry['label'] == 'new' else 'Previously shared reminder'
            card = prefix + f'<i>{label}</i>\n' + _format_job(Job(**entry['job']))
            if len(text) + len(card) + 2 > 3800:
                chunks.append({'text': text, 'new_jobs': new_jobs})
                text, new_jobs = '<b>Digest continued</b>', []
                if not prefix:
                    card = f'<b>{heading}</b>\n' + card
            if len(card) > 3500:
                raise ValueError('Job card exceeds safe message length')
            text += '\n\n' + card
            if entry['label'] == 'new':
                new_jobs.append(entry['job'])
    chunks.append({'text': text, 'new_jobs': new_jobs})
    return chunks

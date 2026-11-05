"""Offline regressions for exact public identity and durable age evidence."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from boards import Job
import research_collector as c
from test_filters import DESCRIPTION

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def sample(**changes):
    fields = dict(id='sample', title='Senior Growth Marketing Manager', company='Example',
        location='Remote - Worldwide', url='https://jobs.lever.co/example/123',
        source='lever/example', posted='2026-11-01', description=DESCRIPTION,
        current_open_verified=True, employer_country='US',
        employer_country_source='https://example.org/about', employer_country_verified_at='2026-11-04')
    fields.update(changes)
    return Job(**fields)


class PublicIdentityTests(unittest.TestCase):
    def test_distinct_supported_identities_survive(self):
        for template in ('https://example.org/careers#/jobs/{}',
                         'https://example.org/careers#!/positions/{}/apply',
                         'https://example.org/careers#job-{}',
                         'https://example.org/careers#jobId={}',
                         'https://example.org/careers?requisitionId={}',
                         'https://example.org/careers?req_id={}'): 
            with self.subTest(template=template):
                urls = [template.format(i) for i in ('123', '456')]
                self.assertEqual(urls, list(map(c.public_url, urls)))
                self.assertNotEqual(c.identity(sample(url=urls[0])), c.identity(sample(url=urls[1])))

    def test_identity_query_encoding_and_order_are_not_rewritten(self):
        url = 'https://example.org/jobs?jobId=REQ%2D123&id=456'
        self.assertEqual(url, c.public_url(url))

    def test_secrets_contacts_and_tracking_are_removed(self):
        for suffix in ('?jobId=123&token=secret&email=person%40example.org&utm_source=x',
                       '#/jobs/123?access_token=secret&phone=12345&utm_campaign=x',
                       '#jobId=123&auth=secret&email=person%40example.org'):
            clean = c.public_url('https://example.org/careers' + suffix)
            self.assertTrue(clean)
            for secret in ('secret', 'email', 'phone', 'utm_', 'token', 'auth'):
                self.assertNotIn(secret, clean)
            self.assertIn('123', clean)

    def test_unknown_selectors_are_withheld_not_collapsed(self):
        for suffix in ('?customSelector=123', '?ref=123', '?source=123',
                       '#/custom/123', '#/jobs/123?custom=secret',
                       '?jobId=person%40example.org', '?jobId=https%3A%2F%2Fexample.org',
                       '?jobId=123%26token%3Dsecret'):
            self.assertEqual('', c.public_url('https://example.org/careers' + suffix))

    def test_private_and_malformed_endpoints_are_rejected(self):
        for url in ('http://localhost./job', 'http://sub.localhost/job', 'http://127.1/job',
                    'http://2130706433/job', 'http://10.0.0.1/job', 'http://[::1]/job',
                    'http://metadata.google.internal/job', 'http://printer/job',
                    'https://user:secret@example.org/job', 'https://example.org:bad/job',
                    'https://example.org/job/person%40example.org', 'https://example.org/\njob'):
            self.assertEqual('', c.public_url(url), url)

    def test_plain_anchor_is_ignored(self):
        self.assertEqual('https://example.org/jobs/123', c.public_url('https://example.org/jobs/123#description'))


class AgeIntegrityTests(unittest.TestCase):
    def build(self, item, history):
        with patch.object(c, 'enrich_employers', side_effect=lambda rows: rows):
            return c.build_report([item], history, [], now=NOW)

    def test_current_missing_future_or_invalid_date_not_replaced_by_recent_history(self):
        for posted in ('', None, 'unparseable', '2026-11-07'):
            for verified in (False, True):
                with self.subTest(posted=posted, verified=verified):
                    item = sample(posted=posted, current_open_verified=verified)
                    prior = {c.identity(item): {'first_posted_at': '2026-11-01', 'first_seen_at': '2026-11-01'}}
                    report, state = self.build(item, prior)
                    row = report['candidates'][0]
                    self.assertEqual(posted, row['posted'])
                    self.assertEqual('unknown', row['age_status'])
                    if posted == '2026-11-07' or not verified:
                        self.assertEqual('review', row['classification'])
                    self.assertTrue(state['jobs'][c.identity(item)]['first_posted_at'].startswith('2026-11-01'))
                    self.assertEqual(posted, item.posted)

    def test_old_evidence_excludes_missing_future_and_republished_dates(self):
        for field in ('first_posted_at', 'first_seen_at', 'oldest_existence_at'):
            for posted in ('', '2026-11-07', '2026-11-04'):
                with self.subTest(field=field, posted=posted):
                    item = sample(posted=posted)
                    prior = {c.identity(item): {field: '2026-09-01'}}
                    report, state = self.build(item, prior)
                    self.assertEqual([], report['candidates'])
                    self.assertEqual('older_than_30_days', report['rejected_candidates'][0]['age_status'])
                    self.assertTrue(state['jobs'][c.identity(item)]['expired_at'])

    def test_different_requisition_does_not_inherit_age(self):
        item = sample(url='https://example.org/careers#/jobs/456')
        old = sample(url='https://example.org/careers#/jobs/123')
        rows, history = c.reconcile([item], {c.identity(old): {'first_posted_at': '2026-09-01'}}, NOW)
        self.assertEqual('2026-11-01T00:00:00+00:00', rows[0].posted)
        self.assertIsNone(history[c.identity(item)]['expired_at'])

    def test_oldest_posting_and_expiry_never_reset(self):
        item = sample()
        key = c.identity(item)
        prior = {key: {'first_seen_at': '2026-09-01', 'first_posted_at': '2026-09-01', 'expired_at': '2026-10-05'}}
        _, history = c.reconcile([item], prior, NOW)
        self.assertTrue(history[key]['first_posted_at'].startswith('2026-09-01'))
        self.assertEqual('2026-10-05', history[key]['expired_at'])
        self.assertEqual(prior, c.reconcile([], prior, NOW)[1])

    def test_unsupported_candidate_url_is_counted_and_flagged_without_exposure(self):
        item = sample(url='https://example.org/careers?customSelector=private-value')
        report, _ = self.build(item, {})
        self.assertEqual([], report['candidates'])
        self.assertEqual(1, report['counts']['withheld_public_urls'])
        self.assertEqual(1, report['coverage']['withheld_public_urls'])
        self.assertIn('1 non-rejected listings were withheld', report['coverage']['url_warning'])
        self.assertNotIn('private-value', json.dumps(report))
        self.assertIn('1 non-rejected listings were withheld', c.markdown(report))
        rejected, _ = self.build(sample(url=item.url, posted='2026-09-01'), {})
        self.assertEqual(0, rejected['counts']['withheld_public_urls'])
        self.assertEqual('', rejected['coverage']['url_warning'])

    def test_partial_expiry_tombstone_alone_cannot_requalify(self):
        item = sample()
        report, state = self.build(item, {c.identity(item): {'expired_at': '2026-10-05'}})
        self.assertEqual([], report['candidates'])
        self.assertEqual('rejected', report['rejected_candidates'][0]['classification'])
        self.assertEqual('2026-10-05', state['jobs'][c.identity(item)]['expired_at'])

    def test_history_merge_retains_oldest_dates_and_any_expiry_in_both_orders(self):
        old = {'key': {'first_posted_at': '2026-09-01', 'first_seen_at': '2026-09-02', 'expired_at': '2026-10-05', 'last_seen_at': '2026-10-05'}}
        new = {'key': {'first_posted_at': '2026-11-01', 'first_seen_at': '2026-11-01', 'expired_at': None, 'last_seen_at': '2026-11-05'}}
        for sources in ((old, new), (new, old)):
            row = c.merge_history(*sources, now=NOW)['key']
            self.assertEqual('2026-09-01', row['first_posted_at'])
            self.assertEqual('2026-09-02', row['first_seen_at'])
            self.assertEqual('2026-10-05', row['expired_at'])
            self.assertEqual('2026-11-05', row['last_seen_at'])

    def test_seed_aliases_merge_without_losing_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE job_history(url,first_seen_at,first_posted_at,expired_at)')
                conn.executemany('INSERT INTO job_history VALUES(?,?,?,?)', [
                    ('https://example.org/jobs/123?utm_source=old', '2026-09-01', '2026-09-01', '2026-10-05'),
                    ('https://example.org/jobs/123', '2026-11-01', '2026-11-01', None)])
            history = c.seed_history(path)
            self.assertEqual(1, len(history))
            row = next(iter(history.values()))
            self.assertEqual('2026-09-01', row['first_posted_at'])
            self.assertEqual('2026-10-05', row['expired_at'])

    def test_seed_merges_only_current_identity_not_entire_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE job_history(url,first_seen_at,first_posted_at,expired_at)')
                conn.executemany('INSERT INTO job_history VALUES(?,?,?,?)', [
                    (f'https://example.org/jobs/{i}', '2026-09-01', '2026-09-01', None)
                    for i in range(100)])
            with patch.object(c, 'merge_history', wraps=c.merge_history) as merge:
                history = c.seed_history(path)
            self.assertEqual(100, len(history))
            self.assertEqual(100, merge.call_count)
            self.assertTrue(all(len(arg) <= 1 for call in merge.call_args_list for arg in call.args))

    def test_collect_merges_legacy_and_saved_history(self):
        item = sample()
        key = c.identity(item)
        old = {key: {'first_posted_at': '2026-09-01', 'expired_at': '2026-10-05'}}
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / 'input.json'
            history_path.write_text(json.dumps({'jobs': {key: {'first_posted_at': '2026-11-01', 'expired_at': None}}}))
            with patch.object(c, 'seed_history', return_value=old), patch.object(c, 'fetch_all', return_value=[item]), patch.object(c, 'snapshot', return_value=[]), patch.object(c, 'enrich_employers', side_effect=lambda rows: rows):
                c.collect(Path(directory) / 'output', history_path, 'unused.db')
            row = json.loads((Path(directory) / 'output/history.json').read_text())['jobs'][key]
            self.assertTrue(row['first_posted_at'].startswith('2026-09-01'))
            self.assertEqual('2026-10-05', row['expired_at'])

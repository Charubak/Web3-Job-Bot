"""Synthetic fixtures only; the regular suite never contacts ATS services."""
import unittest
import hashlib
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timezone
import boards
import storage
from unittest.mock import patch
import httpx

import public_ats as ats
import source_health as health


def response(payload=None, body=None, status=200):
    kwargs = {"json": payload} if body is None else {"content": body}
    return httpx.Response(status, request=httpx.Request("GET", "https://example.test"), **kwargs)


def workable(**changes):
    row = dict(title="Senior Growth Marketing Manager", shortcode="ABC123", country="United States",
               city="New York", telecommuting=True, description="<p>Own growth. US residents only.</p>",
               published_on="2026-10-01", created_at="2025-01-01", locations=[])
    row.update(changes)
    return row


def item(identity=1, **changes):
    data = dict(title="Senior Growth Marketing Manager", link=f"https://crossmint.na.teamtailor.com/jobs/{identity}-growth",
                description="&lt;p&gt;Own growth. US residents only.&lt;/p&gt;", pubDate="Thu, 01 Oct 2026 00:00:00 GMT",
                remoteStatus="fully")
    data.update(changes)
    return '<item>' + ''.join(f'<{k}>{v}</{k}>' for k,v in data.items()) + '''
    <tt:locations><tt:location><tt:name>NYC</tt:name><tt:city>New York</tt:city><tt:country>United States</tt:country></tt:location>
    <tt:location><tt:name>London</tt:name><tt:country>United Kingdom</tt:country></tt:location></tt:locations></item>'''


def feed(*items):
    return '<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>' + ''.join(items) + '</channel></rss>'


class PublicATSTests(unittest.TestCase):
    def setUp(self):
        health.reset()
        self.registry = patch.object(ats, 'WORKABLE_COMPANIES', (("walletconnect", "Reown"),))
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def run_workable(self, rows):
        with patch('boards._get', return_value=response({"jobs": rows})) as get:
            jobs = health.run_source(ats.fetch_workable)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.args[0], 'https://www.workable.com/api/accounts/walletconnect?details=true')
            return jobs

    def test_closed_rows_never_claim_open(self):
        for field, value in (("status", "closed"), ("archived", True), ("is_open", False), ("closed", "true")):
            self.assertEqual(self.run_workable([workable(**{field: value})]), [])
        for fields in ({"status": "closed"}, {"archived": "true"}, {"published": "false"}):
            with patch('boards._get', return_value=response(body=feed(item(**fields)))):
                self.assertEqual(ats.fetch_teamtailor(), [])

    def test_duplicate_closed_status_wins_in_either_order(self):
        for rows in ([workable(), workable(status="closed")], [workable(status="closed"), workable()]):
            self.assertEqual(self.run_workable(rows), [])
        for items in ((item(), item(status="closed")), (item(status="closed"), item())):
            with patch('boards._get', return_value=response(body=feed(*items))):
                self.assertEqual(ats.fetch_teamtailor(), [])

    def test_closure_description_wins_over_live_feed_and_duplicates(self):
        for text in ('Applications are now closed.', 'This position has been filled.'):
            for rows in ([workable(), workable(description=text)], [workable(description=text), workable()]):
                self.assertEqual(self.run_workable(rows), [])
                self.assertIn('ats_closed_item_omitted', health.snapshot()[0]['warnings'])
            for items in ((item(), item(description=text)), (item(description=text), item())):
                with patch('boards._get', return_value=response(body=feed(*items))):
                    self.assertEqual(health.run_source(ats.fetch_teamtailor), [])
                    self.assertIn('ats_closed_item_omitted', health.snapshot()[0]['warnings'])

    def test_teamtailor_query_and_fragment_requisition_selectors_stay_distinct(self):
        for suffix in ('?job=', '#/job/'):
            first = item(link='https://crossmint.na.teamtailor.com/jobs/1-growth' + suffix + 'a')
            second = item(link='https://crossmint.na.teamtailor.com/jobs/1-growth' + suffix + 'b')
            with patch('boards._get', return_value=response(body=feed(first, second))):
                jobs = ats.fetch_teamtailor()
            self.assertEqual(len(jobs), 2)
            self.assertNotEqual(jobs[0].id, jobs[1].id)
            for job in jobs:
                self.assertEqual(job.id, boards._make_id(job.url))

    def test_missing_description_does_not_claim_verified_open(self):
        self.assertFalse(self.run_workable([workable(description="")])[0].current_open_verified)
        with patch('boards._get', return_value=response(body=feed(item(description="")))):
            self.assertFalse(ats.fetch_teamtailor()[0].current_open_verified)

    def test_canonical_ats_aliases_keep_query_identity(self):
        for a, b in (("https://apply.workable.com/walletconnect/j/ABC123/apply/", "https://apply.workable.com/j/ABC123"),
                     ("https://crossmint.na.teamtailor.com/jobs/123-old-title", "https://crossmint.na.teamtailor.com/jobs/123-new-title")):
            self.assertEqual(boards.canonical_job_url(a), boards.canonical_job_url(b))
            self.assertEqual(boards._make_id(a), boards._make_id(b))
            self.assertNotEqual(boards.canonical_job_url(a + '?job=1'), boards.canonical_job_url(b + '?job=2'))
            self.assertNotEqual(boards.canonical_job_url(a + '#/job/1'), boards.canonical_job_url(b + '#/job/2'))
        url = 'https://unknown.teamtailor.com/jobs/123-title'
        self.assertEqual(boards.canonical_job_url(url), url)

    def test_legacy_ats_tombstone_alias_read_only(self):
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        for old, new in (("https://apply.workable.com/walletconnect/j/ABC123/", "https://apply.workable.com/j/ABC123"),
                         ("https://crossmint.na.teamtailor.com/jobs/123-old-title", "https://crossmint.na.teamtailor.com/jobs/123-new-title")):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'history.db'
                conn = sqlite3.connect(path)
                conn.execute('CREATE TABLE job_history (id TEXT PRIMARY KEY, title TEXT, url TEXT, first_seen_at TEXT, first_posted_at TEXT, expired_at TEXT)')
                old_id = hashlib.sha256(old.encode()).hexdigest()[:16]
                conn.execute('INSERT INTO job_history VALUES (?, ?, ?, ?, ?, ?)',
                             (old_id, 'Growth', old, '2026-01-01', None, '2026-02-01'))
                conn.commit(); conn.close()
                before = path.read_bytes()
                job = boards.Job(boards._make_id(new), 'Growth', 'Company', 'Remote', new, 'test', posted='2026-11-08')
                with patch.object(storage, 'DB_PATH', path):
                    reconciled = storage.reconcile_job_history([job], now=now)
                    self.assertTrue(reconciled[0].posted.startswith('2026-01-01'))
                    self.assertEqual(storage.filter_unseen([job]), [])
                self.assertEqual(path.read_bytes(), before)

    def test_workable_dates_country_and_full_description(self):
        job = self.run_workable([workable()])[0]
        self.assertEqual(job.posted, '')
        self.assertEqual(job.published_at_upper_bound, '2026-10-01')
        self.assertTrue(job.current_open_verified)
        self.assertIn('Remote', job.location)
        self.assertIn('United States', job.location)
        self.assertIn('US residents only', job.description)
        self.assertEqual(job.employer_country, '')
        self.assertIn('United States', job.ats_country)

    def test_workable_duplicate_union_including_hidden_and_nested_locations(self):
        rows = [workable(), workable(country='Germany', city='Berlin',
                    locations=[dict(country='France', countryCode='FR', region='Paris', hidden=True)],
                    description='<p>EU residents only.</p>', published_on='2026-09-01')]
        jobs = self.run_workable(rows)
        self.assertEqual(len(jobs), 1)
        for value in ('New York', 'United States', 'Berlin', 'Germany', 'France', 'FR', 'Paris'):
            self.assertIn(value, jobs[0].location)
        self.assertIn('EU residents only.', jobs[0].description)
        self.assertIn('US residents only.', jobs[0].description)
        self.assertEqual(jobs[0].published_at_upper_bound, '2026-09-01')

    def test_workable_no_telecommuting_inference_for_false_or_string(self):
        for value in (False, 'true', None):
            self.assertNotIn('Remote', self.run_workable([workable(telecommuting=value)])[0].location)
        job = self.run_workable([workable(workplace_type='hybrid', telecommuting=True)])[0]
        self.assertIn('Hybrid', job.location)
        self.assertNotIn('Remote', job.location)

    def test_workable_malformed_item_partial(self):
        self.assertEqual(len(self.run_workable([workable(), None, workable(shortcode='../bad')])), 1)
        self.assertEqual(health.snapshot()[0]['status'], 'partial')
        self.assertEqual(health.snapshot()[0]['failures'], 2)

    def test_workable_bad_location_does_not_drop_restrictions(self):
        self.assertEqual(self.run_workable([workable(locations='US only')]), [])
        self.assertEqual(health.snapshot()[0]['status'], 'unavailable')

    def test_workable_invalid_payload_is_failure_not_empty_success(self):
        for payload in ([], {}, {'jobs': None}):
            with patch('boards._get', return_value=response(payload)):
                self.assertEqual(health.run_source(ats.fetch_workable), [])
                self.assertEqual(health.snapshot()[0]['status'], 'unavailable')

    def test_workable_valid_empty_and_cap(self):
        self.assertEqual(self.run_workable([]), [])
        self.assertEqual(health.snapshot()[0]['status'], 'returned')
        with patch.object(ats, 'MAX_WORKABLE_ROWS', 1):
            self.run_workable([workable(), workable(shortcode='DEF')])
        self.assertIn('bounded_workable_row_cap', health.snapshot()[0]['warnings'])

    def test_workable_publication_missing_does_not_use_creation(self):
        job = self.run_workable([workable(published_on=None)])[0]
        self.assertEqual(job.published_at_upper_bound, '')
        self.assertEqual(job.posted, '')

    def test_workable_failure_one_company_keeps_other(self):
        with patch.object(ats, 'WORKABLE_COMPANIES', (("one", "One"), ("two", "Two"))), \
             patch('boards._get', side_effect=[response(status=503), response({'jobs': [workable()]})]):
            jobs = health.run_source(ats.fetch_workable)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, 'Two')
        self.assertEqual(health.snapshot()[0]['status'], 'partial')

    def test_teamtailor_namespace_locations_remote_date(self):
        with patch('boards._get', return_value=response(body=feed(item()))):
            job = health.run_source(ats.fetch_teamtailor)[0]
        self.assertEqual(job.posted, '')
        self.assertEqual(job.published_at_upper_bound, 'Thu, 01 Oct 2026 00:00:00 GMT')
        for value in ('Remote', 'NYC', 'New York', 'United States', 'London', 'United Kingdom'):
            self.assertIn(value, job.location)
        self.assertTrue(job.current_open_verified)
        self.assertIn('US residents only.', job.description)

    def test_teamtailor_pagination_and_duplicate_merge(self):
        with patch.object(ats, 'TEAMTAILOR_PAGE_SIZE', 1), \
             patch('boards._get', side_effect=[response(body=feed(item())), response(body=feed(item(2))), response(body=feed())]) as get:
            jobs = health.run_source(ats.fetch_teamtailor)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(get.call_count, 3)
        self.assertIn('offset=1&per_page=1', get.call_args_list[1].args[0])

    def test_teamtailor_partial_page_failure(self):
        with patch.object(ats, 'TEAMTAILOR_PAGE_SIZE', 1), \
             patch('boards._get', side_effect=[response(body=feed(item())), response(body='not XML')]):
            jobs = health.run_source(ats.fetch_teamtailor)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(health.snapshot()[0]['status'], 'partial')

    def test_teamtailor_repeated_page_bounded(self):
        with patch.object(ats, 'TEAMTAILOR_PAGE_SIZE', 1), \
             patch('boards._get', return_value=response(body=feed(item()))) as get:
            self.assertEqual(len(health.run_source(ats.fetch_teamtailor)), 1)
        self.assertEqual(get.call_count, 2)
        self.assertIn('bounded_teamtailor_pagination_repeated', health.snapshot()[0]['warnings'])

    def test_teamtailor_page_cap(self):
        with patch.object(ats, 'TEAMTAILOR_PAGE_SIZE', 1), \
             patch('boards._get', side_effect=[response(body=feed(item(i))) for i in range(3)]) as get:
            self.assertEqual(len(health.run_source(ats.fetch_teamtailor)), 3)
        self.assertEqual(get.call_count, 3)
        self.assertIn('bounded_teamtailor_page_cap', health.snapshot()[0]['warnings'])

    def test_teamtailor_reject_external_url(self):
        with patch('boards._get', return_value=response(body=feed(item(link='https://evil.test/jobs/1')))):
            self.assertEqual(health.run_source(ats.fetch_teamtailor), [])
        self.assertEqual(health.snapshot()[0]['status'], 'unavailable')

    def test_teamtailor_reject_dtd_html_and_broken_xml(self):
        for body in ('<!DOCTYPE rss><rss><channel/></rss>', '<html/>', '<rss>'):
            with patch('boards._get', return_value=response(body=body)):
                self.assertEqual(health.run_source(ats.fetch_teamtailor), [])
                self.assertEqual(health.snapshot()[0]['status'], 'unavailable')

    def test_teamtailor_same_id_preserves_all_geography_and_text(self):
        other = item(link='https://crossmint.na.teamtailor.com/jobs/1-new-slug',
                     description='Canada residents only.').replace('United States', 'Canada')
        with patch('boards._get', return_value=response(body=feed(item(), other))):
            jobs = health.run_source(ats.fetch_teamtailor)
        self.assertEqual(len(jobs), 1)
        self.assertIn('Canada', jobs[0].location)
        self.assertIn('United States', jobs[0].location)
        self.assertIn('Canada residents only.', jobs[0].description)


if __name__ == '__main__':
    unittest.main()

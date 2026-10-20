"""Offline checks for the maximum 30-day age and durable anti-repost history."""
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from boards import Job, _make_id
from filters import classify_job, filter_jobs
import scraper
import storage

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def job(*, days=20, url='https://jobs.lever.co/example/stable-job', **changes):
    data = dict(
        id=_make_id(url), title='Senior Growth Marketing Manager', company='Example',
        location='Worldwide Remote', url=url, source='lever/example',
        posted=(NOW - timedelta(days=days)).isoformat(),
        description=('Own growth strategy, acquisition and retention. Build experiments with product and '
                     'analytics, define messaging and lead cross-functional product launches. '
                     'We need an experienced hands-on marketer with 6+ years of marketing experience.'),
        employer_country='United Kingdom', employer_country_source='https://example.test/about',
        employer_country_verified_at=NOW.isoformat(),
    )
    data.update(changes)
    return Job(**data)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'seen_jobs.db'
        for target in ('storage.DB_PATH', 'scraper.DB_PATH'):
            p = patch(target, self.db)
            p.start()
            self.addCleanup(p.stop)
        for target in ('filters.datetime', 'storage.datetime', 'scraper.datetime'):
            p = patch(target, FrozenDateTime)
            p.start()
            self.addCleanup(p.stop)

    def tables(self):
        with sqlite3.connect(self.db) as conn:
            return {name: conn.execute(f'SELECT * FROM {name}').fetchall()
                    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_preview_with_no_database_never_creates_state_or_mutates_job(self):
        listing = job()
        original = replace(listing)
        reconciled = storage.reconcile_job_history([listing], now=NOW)
        self.assertEqual(listing, original)
        self.assertIsNot(listing, reconciled[0])
        self.assertEqual('qualified', classify_job(reconciled[0], now=NOW).status)
        self.assertFalse(self.db.exists())

    def test_expired_job_gets_permanent_tombstone_without_marking_sent(self):
        old = job(days=31)
        storage.reconcile_job_history([old], persist=True, now=NOW)
        snapshot = self.tables()
        self.assertEqual([], snapshot['seen_jobs'])
        self.assertEqual([], snapshot['unconfirmed_jobs'])
        self.assertTrue(snapshot['job_history'][0][-1])
        refreshed = replace(old, posted=(NOW + timedelta(days=11)).isoformat())
        later = NOW + timedelta(days=31)
        result = storage.reconcile_job_history([refreshed], now=later)[0]
        self.assertEqual(old.posted, result.posted)
        self.assertEqual('rejected', classify_job(result, now=later).status)
        self.assertEqual([], storage.filter_unseen([refreshed]))
        self.assertEqual(snapshot, self.tables())

    def test_earliest_publication_survives_repost_before_expiry(self):
        initial = job(days=20)
        storage.reconcile_job_history([initial], persist=True, now=NOW)
        # A refreshed date must not buy another 30 days of alert eligibility.
        later = NOW + timedelta(days=11)
        refreshed = replace(initial, posted=(later - timedelta(days=14)).isoformat())
        result = storage.reconcile_job_history([refreshed], persist=True, now=later)[0]
        self.assertEqual(initial.posted, result.posted)
        self.assertEqual('rejected', classify_job(result, now=later).status)
        self.assertTrue(self.tables()['job_history'][0][-1])

    def test_first_observation_bounds_age_without_inventing_publication(self):
        unknown = job(posted='')
        result = storage.reconcile_job_history([unknown], persist=True, now=NOW)[0]
        self.assertEqual('', result.posted)
        self.assertEqual('review', classify_job(result, now=NOW).status)
        later = NOW + timedelta(days=31)
        refreshed = replace(unknown, posted=(later - timedelta(days=14)).isoformat())
        result = storage.reconcile_job_history([refreshed], persist=True, now=later)[0]
        self.assertEqual('rejected', classify_job(result, now=later).status)
        self.assertEqual([], storage.filter_unseen([refreshed]))

    def test_missing_and_future_dates_do_not_reuse_known_date_to_qualify(self):
        storage.reconcile_job_history([job()], persist=True, now=NOW)
        for posted in ('', 'unknown', (NOW + timedelta(seconds=1)).isoformat()):
            with self.subTest(posted=posted):
                result = storage.reconcile_job_history([job(posted=posted)], now=NOW)[0]
                self.assertEqual(posted, result.posted)
                self.assertEqual('review', classify_job(result, now=NOW).status)

    def test_unknown_date_after_expiry_still_rejects(self):
        storage.reconcile_job_history([job(days=31)], persist=True, now=NOW)
        result = storage.reconcile_job_history([job(posted='')], now=NOW)[0]
        self.assertEqual('rejected', classify_job(result, now=NOW).status)

    def test_boundary_exactly_thirty_days_is_not_tombstoned(self):
        result = storage.reconcile_job_history([job(days=30)], persist=True, now=NOW)[0]
        self.assertEqual('qualified', classify_job(result, now=NOW).status)
        self.assertIsNone(self.tables()['job_history'][0][-1])
        result = storage.reconcile_job_history([job(days=30)], persist=True,
                                               now=NOW + timedelta(microseconds=1))[0]
        self.assertEqual('rejected', classify_job(result, now=NOW + timedelta(microseconds=1)).status)
        self.assertTrue(self.tables()['job_history'][0][-1])

    def test_new_jobs_qualify_immediately_without_becoming_seen(self):
        for days in (0, 1, 3, 13, 14, 30):
            with self.subTest(days=days):
                initial = job(days=days, url=f'https://jobs.lever.co/example/age-{days}')
                result = storage.reconcile_job_history([initial], persist=True, now=NOW)[0]
                self.assertEqual('qualified', classify_job(result, now=NOW).status)
                self.assertEqual([result], storage.filter_unseen([result]))
                self.assertEqual([], self.tables()['seen_jobs'])
                self.assertEqual([], self.tables()['unconfirmed_jobs'])
                self.assertTrue(all(row[-1] is None for row in self.tables()['job_history']))

    def test_new_post_expires_only_after_thirty_days_and_cannot_be_rejuvenated(self):
        initial = job(days=0)
        storage.reconcile_job_history([initial], persist=True, now=NOW)
        for elapsed, expected in ((0, 'qualified'), (1, 'qualified'), (13, 'qualified'),
                                  (14, 'qualified'), (30, 'qualified'), (31, 'rejected')):
            with self.subTest(elapsed=elapsed):
                later = NOW + timedelta(days=elapsed)
                refreshed = replace(initial, posted=later.isoformat())
                result = storage.reconcile_job_history([refreshed], persist=True, now=later)[0]
                self.assertEqual(initial.posted, result.posted)
                self.assertEqual(expected, classify_job(result, now=later).status)
        self.assertTrue(self.tables()['job_history'][0][-1])
        self.assertEqual([], storage.filter_unseen([initial]))

    def test_preview_selects_newest_qualifying_jobs_first(self):
        ages = (30, 14, 1, 31, 13, 3, 0)
        listings = [job(days=days, url=f'https://jobs.lever.co/example/age-{days}') for days in ages]
        with patch('scraper.fetch_all', return_value=listings), \
             patch('scraper.enrich_employers', side_effect=lambda jobs: jobs), \
             patch('notifier.send_jobs') as send:
            report = scraper.main(max_alerts=3)
        send.assert_not_called()
        self.assertEqual(6, report['counts']['qualified'])
        self.assertEqual(1, report['counts']['rejected'])
        self.assertEqual(3, report['counts']['selected'])
        self.assertEqual(['https://jobs.lever.co/example/age-0',
                          'https://jobs.lever.co/example/age-1',
                          'https://jobs.lever.co/example/age-3'],
                         [row['url'] for row in report['qualified']])
        self.assertFalse(self.db.exists())

    def test_tracking_alias_cannot_reset_expiry_and_separate_requisition_survives(self):
        old = job(days=31, url='https://jobs.lever.co/example/stable-job?utm_source=old#apply')
        storage.reconcile_job_history([old], persist=True, now=NOW)
        alias = job(url='https://jobs.lever.co/example/stable-job?utm_source=new')
        new = job(url='https://jobs.lever.co/example/new-requisition')
        result = storage.reconcile_job_history([alias, new], now=NOW)
        self.assertEqual(['rejected', 'qualified'], [classify_job(j, now=NOW).status for j in result])
        self.assertEqual([new], storage.filter_unseen([alias, new]))

    def test_legacy_seen_and_pending_tracking_aliases_remain_suppressed(self):
        for table in ('seen_jobs', 'unconfirmed_jobs'):
            with self.subTest(table=table):
                self.db.unlink(missing_ok=True)
                storage.initialize_state()
                listing = job()
                with sqlite3.connect(self.db) as conn:
                    conn.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?)',
                                 ('legacy-raw-url-hash', listing.title,
                                  listing.url + '?utm_source=before#apply', NOW.isoformat()))
                before = self.db.read_bytes()
                self.assertEqual([], storage.filter_unseen([listing]))
                self.assertEqual(before, self.db.read_bytes())

    def test_preview_does_not_migrate_legacy_database(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE seen_jobs (id TEXT PRIMARY KEY, title TEXT, url TEXT, seen_at TEXT)')
        before = self.db.read_bytes()
        result = storage.reconcile_job_history([job(days=31)], now=NOW)
        self.assertEqual('rejected', classify_job(result[0], now=NOW).status)
        self.assertEqual(before, self.db.read_bytes())
        self.assertNotIn('job_history', self.tables())

    def test_legacy_delivery_date_prevents_month_later_replay_after_timestamp_refresh(self):
        for table, date_column in (('seen_jobs', 'seen_at'), ('unconfirmed_jobs', 'attempted_at')):
            with self.subTest(table=table):
                self.db.unlink(missing_ok=True)
                storage.initialize_state()
                listing = job()
                with sqlite3.connect(self.db) as conn:
                    conn.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?)',
                                 ('legacy-url-id', listing.title, listing.url + '?utm_source=old',
                                  (NOW - timedelta(days=31)).isoformat()))
                before = self.tables()[table]
                result = storage.reconcile_job_history([listing], persist=True, now=NOW)[0]
                self.assertEqual('rejected', classify_job(result, now=NOW).status)
                self.assertTrue(self.tables()['job_history'][0][-1])
                self.assertEqual(before, self.tables()[table])

    def test_repeated_alias_order_cannot_rejuvenate_old_job(self):
        for postings in ([job(), job(days=31)], [job(days=31), job()]):
            result = storage.reconcile_job_history(postings, now=NOW)
            self.assertEqual(['rejected', 'rejected'], [classify_job(j, now=NOW).status for j in result])

    def test_history_write_preserves_seen_pending_and_activation(self):
        storage.mark_seen([job(url='https://example.test/seen')])
        pending = job(url='https://example.test/pending')
        storage.mark_unconfirmed([pending])
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE activation_test (id TEXT PRIMARY KEY, state TEXT)')
            conn.execute("INSERT INTO activation_test VALUES ('active', 'acknowledged')")
        before = self.tables()
        storage.reconcile_job_history([job(days=31), pending], persist=True, now=NOW)
        after = self.tables()
        for table in ('seen_jobs', 'unconfirmed_jobs', 'activation_test'):
            self.assertEqual(before[table], after[table])
        self.assertTrue(storage.are_unconfirmed([pending]))

    def test_existing_expiry_preview_leaves_database_byte_identical(self):
        storage.reconcile_job_history([job()], persist=True, now=NOW)
        before = self.db.read_bytes()
        result = storage.reconcile_job_history([job()], now=NOW + timedelta(days=31))[0]
        self.assertEqual('rejected', classify_job(result, now=NOW + timedelta(days=31)).status)
        self.assertEqual(before, self.db.read_bytes())

    def test_refresh_history_requires_existing_state_before_fetch(self):
        with patch('scraper.fetch_all') as fetch:
            with self.assertRaisesRegex(scraper.SetupError, 'existing state'):
                scraper.main(refresh_history=True)
        fetch.assert_not_called()

    def test_refresh_history_only_persists_public_age_history(self):
        storage.mark_seen([job(url='https://example.test/seen')])
        pending = job(url='https://example.test/pending')
        storage.mark_unconfirmed([pending])
        before = self.tables()
        report_path = Path(self.tmp.name) / 'report.json'
        with patch('scraper.fetch_all', return_value=[job(days=31)]), \
             patch('scraper.enrich_employers', side_effect=lambda jobs: jobs), \
             patch('notifier.send_jobs') as send:
            report = scraper.main(refresh_history=True, report_path=str(report_path))
        send.assert_not_called()
        self.assertEqual('refresh-history', report['mode'])
        self.assertEqual(1, report['counts']['rejected'])
        self.assertEqual(0, report['counts']['selected'])
        self.assertTrue(self.tables()['job_history'][0][-1])
        for table in ('seen_jobs', 'unconfirmed_jobs'):
            self.assertEqual(before[table], self.tables()[table])
        self.assertEqual(report, json.loads(report_path.read_text()))
        for field in ('posted', 'company', 'location', 'reason'):
            self.assertTrue(report['rejected'][0][field])

    def test_prepare_persists_expired_jobs_even_without_any_alerts(self):
        storage.initialize_state()
        plan = Path(self.tmp.name) / 'plan.json'
        with patch('scraper.fetch_all', return_value=[job(days=31)]), \
             patch('scraper.enrich_employers', side_effect=lambda jobs: jobs), \
             patch('notifier.send_jobs') as send:
            scraper.main(prepare_path=str(plan))
        send.assert_not_called()
        self.assertEqual([], json.loads(plan.read_text())['jobs'])
        self.assertTrue(self.tables()['job_history'][0][-1])
        self.assertEqual([], self.tables()['seen_jobs'])
        self.assertEqual([], self.tables()['unconfirmed_jobs'])

    def test_history_review_reports_all_qualified_including_seen_and_over_alert_cap(self):
        listings = [job(url=f'https://jobs.lever.co/example/{n}') for n in range(12)]
        storage.mark_seen(listings[:1])
        with patch('scraper.fetch_all', return_value=listings), \
             patch('scraper.enrich_employers', side_effect=lambda jobs: jobs), \
             patch('notifier.send_jobs') as send:
            report = scraper.main(refresh_history=True, max_alerts=2)
        send.assert_not_called()
        self.assertEqual(12, report['counts']['qualified'])
        self.assertEqual(11, report['counts']['new'])
        self.assertEqual(2, report['counts']['selected'])
        self.assertEqual(12, len(report['qualified']))
        self.assertTrue(all(item['reason'] for item in report['qualified']))

    def test_preview_pipeline_never_creates_history_or_delivery_state(self):
        with patch('scraper.fetch_all', return_value=[job(days=31)]), \
             patch('scraper.enrich_employers', side_effect=lambda jobs: jobs), \
             patch('notifier.send_jobs') as send:
            scraper.main()
        send.assert_not_called()
        self.assertFalse(self.db.exists())

    def test_history_refresh_cannot_be_combined_with_delivery_or_initialization(self):
        for changes in ({'initialize': True}, {'prepare_path': 'plan.json'}, {'dry_run': False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                scraper.main(refresh_history=True, **changes)


if __name__ == '__main__':
    unittest.main()

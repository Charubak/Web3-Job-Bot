import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import daily_digest as daily
import source_health
import storage
import scraper
import notifier

REPORT = {'counts': {'raw': 100, 'qualified': 4, 'review': 15, 'new': 0, 'selected': 0},
          'sources': [{'source': 'cryptojobslist', 'count': 0, 'status': 'unavailable'},
                      {'source': 'twitter_bing', 'count': 0, 'status': 'returned'}]}


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        for target, value in [('storage.DB_PATH', self.path/'seen_jobs.db'),
                              ('scraper.DB_PATH', self.path/'seen_jobs.db'),
                              ('daily_digest.PLAN', self.path/'daily_plan.json')]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        storage.initialize_state()
        p = patch('daily_digest.verify_activation'); p.start(); self.addCleanup(p.stop)

    def prepare_test(self):
        with patch('scraper.main', return_value=REPORT):
            self.assertTrue(daily.prepare(test=True))

    def test_india_time_boundary(self):
        self.assertIsNone(daily.daily_key(datetime(2026, 10, 22, 7, 29, tzinfo=timezone.utc)))
        self.assertEqual('2026-10-22', daily.daily_key(datetime(2026, 10, 22, 7, 30, tzinfo=timezone.utc)))
        self.assertIsNone(daily.daily_key(datetime(2026, 10, 22, 18, 30, tzinfo=timezone.utc)))

    def test_old_morning_time_is_blocked_and_late_recovery_uses_same_key(self):
        self.assertIsNone(daily.daily_key(datetime(2026, 10, 23, 3, 30, tzinfo=timezone.utc)))
        self.assertEqual('2026-10-23', daily.daily_key(datetime(2026, 10, 23, 12, 30, tzinfo=timezone.utc)))

    def test_empty_partial_status_truthfully_distinguishes_missing_coverage(self):
        text = daily.render(REPORT)
        self.assertIn('No new verified matches in the sources checked', text)
        self.assertIn('Partial coverage', text)
        self.assertIn('cryptojobslist', text)
        self.assertIn('No listings returned by: twitter_bing', text)
        self.assertNotIn('No opportunities', text)

    def test_all_sources_empty_is_not_claimed_complete(self):
        report = {**REPORT, 'counts': {**REPORT['counts'], 'raw': 0}}
        self.assertIn('does not establish', daily.render(report))

    def test_test_delivery_is_once_and_does_not_deliver_jobs(self):
        self.prepare_test()
        with patch('notifier._send') as send, patch('scraper.deliver_prepared') as jobs:
            daily.deliver()
            send.assert_called_once()
            self.assertIn('TEST', send.call_args.args[0])
            jobs.assert_not_called()
            self.assertFalse(daily.prepare(test=True))
            with self.assertRaisesRegex(RuntimeError, 'refusing replay'):
                daily.deliver()
            self.assertEqual(1, send.call_count)

    def test_pending_preparation_cannot_be_overwritten(self):
        self.prepare_test()
        with patch('scraper.main') as fetch:
            with self.assertRaisesRegex(RuntimeError, 'pending/unconfirmed'):
                daily.prepare(test=True)
            fetch.assert_not_called()

    def test_unknown_outcome_keeps_fence(self):
        self.prepare_test()
        with patch('notifier._send', side_effect=notifier.DeliveryUnconfirmed('timeout')):
            with self.assertRaises(notifier.DeliveryUnconfirmed): daily.deliver()
        with self.assertRaisesRegex(RuntimeError, 'pending/unconfirmed'):
            daily.prepare(test=True)

    def test_explicit_rejection_can_be_prepared_again(self):
        self.prepare_test()
        with patch('notifier._send', side_effect=notifier.DeliveryRejected('400')):
            with self.assertRaises(notifier.DeliveryRejected): daily.deliver()
        self.prepare_test()

    def test_tampered_plan_cannot_send(self):
        self.prepare_test()
        data = json.loads(daily.PLAN.read_text()); data['text'] = 'tampered'
        daily.PLAN.write_text(json.dumps(data))
        with patch('notifier._send') as send:
            with self.assertRaisesRegex(RuntimeError, 'mismatched'): daily.deliver()
            send.assert_not_called()

    def test_test_mode_keeps_existing_job_tables_unchanged(self):
        from boards import Job
        j = Job('old', 'Growth Manager', 'Example', 'Worldwide', 'https://example.org/job', 'example')
        storage.mark_seen([j])
        self.prepare_test()
        with patch('notifier._send'): daily.deliver()
        self.assertEqual([], storage.filter_unseen([j]))
        with daily.connect() as conn:
            self.assertEqual(1, conn.execute('SELECT count(*) FROM seen_jobs').fetchone()[0])
            self.assertEqual(0, conn.execute('SELECT count(*) FROM job_history').fetchone()[0])
            self.assertEqual(0, conn.execute('SELECT count(*) FROM unconfirmed_jobs').fetchone()[0])

    def test_daily_empty_search_prepares_status_instead_of_silent_exit(self):
        with patch('scraper.fetch_all', return_value=[]):
            report = scraper.main(allow_empty=True, prepare_path=str(self.path/'pending_plan.json'))
        self.assertEqual(0, report['counts']['new'])

    def test_normal_daily_status_and_selected_jobs_run_once(self):
        report = {**REPORT, 'counts': {**REPORT['counts'], 'new': 2, 'selected': 2}}
        with patch('daily_digest.daily_key', return_value='2026-10-23'), patch('scraper.main', return_value=report), patch('notifier._send') as send, patch('scraper.deliver_prepared') as jobs:
            self.assertTrue(daily.prepare())
            daily.deliver()
            self.assertFalse(daily.prepare())
            send.assert_called_once()
            jobs.assert_called_once()

    def test_normal_empty_daily_status_sends_even_when_silent_flag_is_true(self):
        with patch('daily_digest.daily_key', return_value='2026-10-23'), patch('scraper.main', return_value=REPORT), patch('notifier.SILENT_IF_EMPTY', True), patch('notifier._send') as send, patch('scraper.deliver_prepared') as jobs:
            self.assertTrue(daily.prepare())
            daily.deliver()
            send.assert_called_once()
            jobs.assert_not_called()

    def test_age_unknown_is_explicit(self):
        from types import SimpleNamespace
        text = notifier._format_job(SimpleNamespace(title='Content Manager',company='Example',url='https://example.org',posted=''))
        self.assertIn('original posting date unknown', text)


class SourceHealthTests(unittest.TestCase):
    def setUp(self): source_health.reset()

    def test_partial_and_failed_are_distinct_from_returned_zero(self):
        def fetch_partial():
            source_health.record_failure(); return [1]
        def fetch_failed():
            source_health.record_failure(); return []
        def fetch_zero(): return []
        for fn in (fetch_partial, fetch_failed, fetch_zero): source_health.run_source(fn)
        records = {s['source']: s for s in source_health.snapshot()}
        self.assertEqual('partial', records['partial']['status'])
        self.assertEqual('unavailable', records['failed']['status'])
        self.assertEqual('returned', records['zero']['status'])

    def test_uncaught_adapter_exception_records_unavailable(self):
        def fetch_broken(): raise ValueError('private text')
        with self.assertRaises(ValueError): source_health.run_source(fetch_broken)
        self.assertEqual('unavailable', source_health.snapshot()[0]['status'])
        self.assertNotIn('private text', str(source_health.snapshot()))

if __name__ == '__main__': unittest.main()

class MorningDispatchTests(unittest.TestCase):
    def test_due_uses_india_date_and_no_repeat_after_dispatch(self):
        from scripts.morning_dispatch import due
        before = datetime(2026, 10, 23, 7, 29, tzinfo=timezone.utc)
        at = datetime(2026, 10, 23, 7, 30, tzinfo=timezone.utc)
        self.assertIsNone(due(before, {}))
        self.assertEqual('2026-10-23', due(at, {}))
        self.assertIsNone(due(at, {'dispatched': '2026-10-23'}))
        self.assertIsNone(due(datetime(2026, 10, 22, 10, tzinfo=timezone.utc), {}))

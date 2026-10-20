import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from boards import Job
import scraper
import storage
import notifier


def job(n=1):
    return Job(str(n), 'Senior Growth Marketing Manager', 'Example', 'Worldwide Remote',
               f'https://example.test/jobs/{n}', 'lever/example', posted='1d')


def results(qualified=(), review=(), rejected=()):
    return SimpleNamespace(qualified=list(qualified), review=list(review), rejected=list(rejected))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = self.path / 'seen_jobs.db'
        self.patches = [patch('storage.DB_PATH', self.db), patch('scraper.DB_PATH', self.db),
                        patch('scraper.COMPANIES_CACHE', self.path / 'companies.json')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_default_preview_never_sends_or_writes_state(self):
        with patch('scraper.fetch_all', return_value=[job()]), patch('scraper.filter_jobs', return_value=results([job()])), patch('notifier.send_jobs') as send:
            scraper.main()
        send.assert_not_called()
        self.assertEqual(list(self.path.iterdir()), [])

    def test_normal_preview_keeps_qualified_age_and_remote_uncertainty_in_report(self):
        decision=SimpleNamespace(reason='Current opening; original date and country eligibility unconfirmed', timezone_note='US overlap')
        with patch('scraper.fetch_all',return_value=[job()]), patch('scraper.filter_jobs',return_value=results([job()])), patch('scraper.classify_job',return_value=decision):
            report=scraper.main()
        self.assertEqual(report['qualified'][0]['reason'],decision.reason)
        self.assertEqual(report['qualified'][0]['timezone_note'],decision.timezone_note)
        self.assertEqual(list(self.path.iterdir()),[])

    def test_preview_preserves_existing_dedup_state(self):
        storage.mark_seen([job()])
        before = self.db.read_bytes()
        with patch('scraper.fetch_all', return_value=[job(2)]), patch('scraper.filter_jobs', return_value=results([job(2)])):
            scraper.main()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(storage.filter_unseen([job(2)]), [job(2)])

    def test_initialize_baselines_only_qualified_without_delivery(self):
        review = SimpleNamespace(job=job(2), reason='Employer base unknown', timezone_note='')
        with patch('scraper.fetch_all', return_value=[job(), job(2)]), patch('scraper.filter_jobs', return_value=results([job()], [review])), patch('notifier.send_jobs') as send:
            report = scraper.main(initialize=True)
        send.assert_not_called()
        self.assertEqual(storage.filter_unseen([job(), job(2)]), [job(2)])
        self.assertEqual(report['counts']['review'], 1)

    def test_missing_state_blocks_delivery_before_fetch(self):
        with patch('config.validate_telegram_config'), patch('scraper.fetch_all') as fetch:
            with self.assertRaisesRegex(RuntimeError, 'state is missing'):
                scraper.main(dry_run=False, require_state=True)
        fetch.assert_not_called()

    def test_zero_sources_is_visible_failure_not_success(self):
        with patch('scraper.fetch_all', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'No jobs'):
                scraper.main()

    def test_delivery_state_written_only_after_acknowledgment(self):
        storage.initialize_state()
        def fake_send(jobs, on_sent, on_attempt, on_rejected):
            on_sent(jobs[:1])
            raise RuntimeError('Second message failed')
        with patch('config.validate_telegram_config'), patch('scraper.fetch_all', return_value=[job(), job(2)]), patch('scraper.filter_jobs', return_value=results([job(), job(2)])), patch('notifier.send_jobs', side_effect=fake_send):
            with self.assertRaises(RuntimeError):
                scraper.main(dry_run=False, require_state=True)
        self.assertEqual(len(storage.filter_unseen([job(), job(2)])), 1)

    def test_no_review_listing_is_delivered(self):
        storage.initialize_state()
        review = SimpleNamespace(job=job(2), reason='Remote geography unknown', timezone_note='')
        with patch('config.validate_telegram_config'), patch('scraper.fetch_all', return_value=[job(), job(2)]), patch('scraper.filter_jobs', return_value=results([job()], [review])), patch('notifier.send_jobs') as send:
            scraper.main(dry_run=False)
        self.assertEqual(send.call_args.args[0], [job()])

    def test_unconfirmed_delivery_is_not_retried_by_future_runs(self):
        storage.mark_unconfirmed([job()])
        self.assertEqual(storage.filter_unseen([job(), job(2)]), [job(2)])
        storage.mark_seen([job()])
        self.assertEqual(storage.filter_unseen([job()]), [])

    def test_prepare_writes_fence_and_plan_without_delivery(self):
        storage.initialize_state()
        plan = self.path / 'plan.json'
        with patch('scraper.fetch_all', return_value=[job()]), patch('scraper.filter_jobs', return_value=results([job()])), patch('notifier.send_jobs') as send:
            scraper.main(prepare_path=str(plan))
        send.assert_not_called()
        self.assertEqual(len(json.loads(plan.read_text())['jobs']), 1)
        self.assertTrue(storage.are_unconfirmed([job()]))
        self.assertEqual(storage.filter_unseen([job()]), [])

    def test_prepared_delivery_requires_matching_fence(self):
        plan = self.path / 'plan.json'
        plan.write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(), 'jobs': [asdict(job())]}))
        with patch('config.validate_telegram_config'), patch('notifier.send_jobs') as send:
            with self.assertRaisesRegex(scraper.SetupError, 'fence is missing'):
                scraper.deliver_prepared(str(plan))
        send.assert_not_called()

    def test_fence_exists_before_request_and_survives_ack_write_failure(self):
        def verify_fence(text):
            self.assertTrue(storage.are_unconfirmed([job()]))
        with patch('notifier._format_job', return_value='safe'), patch('notifier._send', side_effect=verify_fence):
            with self.assertRaises(RuntimeError):
                notifier.send_jobs([job()], on_attempt=storage.mark_unconfirmed,
                                   on_sent=lambda batch: (_ for _ in ()).throw(RuntimeError('disk failure')),
                                   on_rejected=storage.clear_unconfirmed)
        self.assertTrue(storage.are_unconfirmed([job()]))
        self.assertEqual(storage.filter_unseen([job()]), [])

    def test_confirmed_rejection_releases_fence_without_marking_seen(self):
        with patch('notifier._format_job', return_value='safe'), patch('notifier._send', side_effect=notifier.DeliveryRejected('400')):
            with self.assertRaises(notifier.DeliveryRejected):
                notifier.send_jobs([job()], on_attempt=storage.mark_unconfirmed,
                                   on_sent=storage.mark_seen, on_rejected=storage.clear_unconfirmed)
        self.assertFalse(storage.are_unconfirmed([job()]))
        self.assertEqual(storage.filter_unseen([job()]), [job()])

    def test_uncertain_request_keeps_pre_request_fence(self):
        with patch('notifier._format_job', return_value='safe'), patch('notifier._send', side_effect=notifier.DeliveryUnconfirmed('timeout')):
            with self.assertRaises(notifier.DeliveryUnconfirmed):
                notifier.send_jobs([job()], on_attempt=storage.mark_unconfirmed,
                                   on_sent=storage.mark_seen, on_rejected=storage.clear_unconfirmed)
        self.assertTrue(storage.are_unconfirmed([job()]))
        self.assertEqual(storage.filter_unseen([job()]), [])

    def test_reading_large_dedup_list_is_batched(self):
        jobs = [job(n) for n in range(1801)]
        storage.mark_seen(jobs[:1000])
        self.assertEqual(storage.filter_unseen(jobs), jobs[1000:])


class NotifierTests(unittest.TestCase):
    def test_successful_chunk_recorded_before_later_failure(self):
        acknowledged = []
        with patch('notifier._format_job', return_value='x' * 2500), patch('notifier._send', side_effect=[None, RuntimeError('failure')]), patch('notifier.time.sleep'):
            with self.assertRaises(RuntimeError):
                notifier.send_jobs([job(), job(2)], on_sent=lambda batch: acknowledged.extend(batch))
        self.assertEqual(len(acknowledged), 1)

    def test_network_error_is_not_retried_and_token_not_leaked(self):
        import httpx
        with patch('notifier.validate_telegram_config'), patch('notifier.httpx.post', side_effect=httpx.ReadTimeout('secret-token-in-url')) as post:
            with self.assertRaises(RuntimeError) as caught:
                notifier._send('test')
        self.assertEqual(post.call_count, 1)
        self.assertNotIn('secret-token', str(caught.exception))

    def test_message_escapes_markup_and_unsafe_url(self):
        sample = job()
        sample.title = '<b>Injected</b>'
        sample.url = 'javascript:alert(1)'
        decision = SimpleNamespace(reason='Worldwide remote; foreign employer', timezone_note='4 hours US overlap')
        with patch('notifier.classify_job', return_value=decision):
            text = notifier._format_job(sample)
        self.assertIn('&lt;b&gt;', text)
        self.assertNotIn('javascript:', text)
        self.assertNotIn('US overlap', text)  # Detailed eligibility stays in the audit report.

    def test_ambiguous_send_quarantines_batch_without_marking_sent(self):
        ack, ambiguous = [], []
        with patch('notifier._send', side_effect=notifier.DeliveryUnconfirmed('timeout')), patch('notifier._format_job', return_value='safe'):
            with self.assertRaises(notifier.DeliveryUnconfirmed):
                notifier.send_jobs([job()], on_sent=ack.extend, on_unconfirmed=ambiguous.extend)
        self.assertEqual(ack, [])
        self.assertEqual(ambiguous, [job()])

    def test_failed_telegram_response_does_not_acknowledge(self):
        response = SimpleNamespace(status_code=400, is_error=True, json=lambda: {'ok': False})
        ack = []
        with patch('notifier.validate_telegram_config'), patch('notifier.httpx.post', return_value=response), patch('notifier._format_job', return_value='safe'):
            with self.assertRaises(RuntimeError):
                notifier.send_jobs([job()], on_sent=lambda batch: ack.extend(batch))
        self.assertEqual(ack, [])


if __name__ == '__main__':
    unittest.main()

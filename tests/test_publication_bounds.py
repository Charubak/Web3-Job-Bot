"""Last publication establishes an age bound, never a qualifying original date."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from boards import Job, _make_id
import boards
import storage
from filters import classify_job

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)

def job(days, posted=''):
    url = 'https://jobs.ashbyhq.com/example/stable-role'
    return Job(_make_id(url), 'Senior Growth Marketing Manager', 'Example', 'Remote Worldwide',
        url, 'ashby/example', posted=posted,
        published_at_upper_bound=(NOW-timedelta(days=days)).isoformat(),
        description='Own growth strategy, acquisition and retention. Build experiments with product and analytics, define messaging and lead cross-functional product launches. We need a hands-on marketer with 6+ years of marketing experience.',
        employer_country='United Kingdom', employer_country_source='https://example.test/about',
        employer_country_verified_at=NOW.isoformat())

class PublicationBoundsTests(unittest.TestCase):
    def test_recent_last_publication_does_not_establish_original_date(self):
        for days in (1, 20, 30, -1):
            with self.subTest(days=days):
                result = classify_job(job(days), now=NOW)
                self.assertEqual('review', result.status)
                self.assertIn('Posting date is missing', result.reason)

    def test_old_last_publication_rejects_even_new_claimed_original_date(self):
        for posted in ('', (NOW-timedelta(days=20)).isoformat()):
            self.assertEqual('rejected', classify_job(job(31, posted), now=NOW).status)

    def test_recent_or_future_bound_never_rejects_valid_original(self):
        for days in (1, -1):
            self.assertEqual('qualified', classify_job(job(days, (NOW-timedelta(days=20)).isoformat()), now=NOW).status)

    def test_old_existence_bound_persists_expiry_without_fabricating_original(self):
        with tempfile.TemporaryDirectory() as tmp, patch('storage.DB_PATH', Path(tmp)/'state.db'):
            first = storage.reconcile_job_history([job(53)], persist=True, now=NOW)
            self.assertEqual('rejected', classify_job(first[0], now=NOW).status)
            with sqlite3.connect(storage.DB_PATH) as conn:
                original, expired = conn.execute('SELECT first_posted_at, expired_at FROM job_history').fetchone()
            self.assertIsNone(original)
            self.assertTrue(expired)
            refreshed = storage.reconcile_job_history([job(1)], persist=True, now=NOW+timedelta(days=40))
            self.assertEqual('rejected', classify_job(refreshed[0], now=NOW+timedelta(days=40)).status)
            self.assertEqual([], storage.filter_unseen(refreshed))

    def test_same_identity_bound_survives_fetch_dedup_both_orders(self):
        for rows in ([job(20),job(53)], [job(53),job(20)]):
            with patch.object(boards, 'BOARDS', [lambda: rows]):
                fetched = boards.fetch_all(now=NOW)
            self.assertEqual(1, len(fetched))
            self.assertEqual('', fetched[0].posted)
            self.assertEqual('rejected', classify_job(fetched[0], now=NOW).status)
            self.assertEqual('', rows[0].posted)

if __name__ == '__main__':
    unittest.main()

"""Exact-role date/expiry regressions for old BlockAce index cards."""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

import boards
import source_health


NOW = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)
URL = 'https://blockace.io/jobs/senior-marketing-manager-at-seedifyfund-348093'


def job():
    return boards.Job(boards._make_id(URL), 'Senior Marketing Manager', 'Seedify.fund',
                      'Remote', URL, 'blockace.io', description='Index summary')


def schema(**changes):
    row = {'@context': 'http://schema.org', '@type': 'JobPosting', 'url': URL,
           'title': 'Senior Marketing Manager', 'hiringOrganization': {'name': 'Seedify.fund'},
           'datePosted': '2021-09-23T18:05:08.000Z', 'validThrough': '2022-01-01T18:05:08.000Z',
           'description': '<p>Own marketing strategy.</p><p>Must reside in Canada.</p>'}
    row.update(changes)
    return '<script type="application/ld+json">' + json.dumps(row) + '</script>'


class BlockAceDateTests(unittest.TestCase):
    def setUp(self):
        source_health.reset()

    def fetch(self, html=None, listing=None):
        reply = httpx.Response(200, text=html if html is not None else schema(),
                               request=httpx.Request('GET', URL))
        def fetch_example():
            row = boards._blockace_detail(listing or job(), NOW)
            return [row] if row else []
        with patch('boards._get', return_value=reply):
            return source_health.run_source(fetch_example)

    def test_actual_2021_posting_expired_in_2022_is_suppressed(self):
        self.assertEqual([], self.fetch())
        self.assertIn('expired_blockace_listing_suppressed', source_health.snapshot()[0]['warnings'])

    def test_original_date_is_retained_even_without_expiry(self):
        row = self.fetch(schema(validThrough=''))[0]
        self.assertTrue(row.posted.startswith('2021-09-23'))
        self.assertIn('Must reside in Canada', row.description)
        self.assertFalse(row.current_open_verified)

    def test_missing_and_future_dates_are_not_replaced_by_collection_time(self):
        for date in ['', 'invalid', '2026-11-07']:
            with self.subTest(date=date):
                self.assertEqual('', self.fetch(schema(datePosted=date, validThrough=''))[0].posted)

    def test_expiry_today_includes_whole_day_when_date_has_no_time(self):
        self.assertEqual(1, len(self.fetch(schema(datePosted='2026-11-01', validThrough='2026-11-05'))))

    def test_exact_expiry_instant_is_no_longer_open(self):
        self.assertEqual([], self.fetch(schema(datePosted='2026-11-01', validThrough='2026-11-05T08:00:00Z')))

    def test_dates_are_not_copied_from_another_requisition_or_employer(self):
        for changed in [{'url': URL + '-other'}, {'title': 'Different role'},
                        {'hiringOrganization': {'name': 'Different employer'}}]:
            with self.subTest(changed=changed):
                row = self.fetch(schema(**changed))[0]
                self.assertEqual('', row.posted)
                self.assertEqual('Index summary', row.description)

    def test_malformed_and_missing_schema_preserves_unknown_evidence(self):
        for html in ['<html>Index only</html>', '<script type="application/ld+json">{broken</script>']:
            with self.subTest(html=html):
                row = self.fetch(html)[0]
                self.assertEqual('', row.posted)
                self.assertIn('blockace_exact_role_metadata_unverified', source_health.snapshot()[0]['warnings'])

    def test_untrusted_detail_url_is_not_requested(self):
        for url in ['http://localhost/jobs/test', 'https://other.test/jobs/test',
                    'https://u:secret@blockace.io/jobs/test']:
            listing = job()
            listing.url = url
            with self.subTest(url=url), patch('boards._get') as get:
                self.assertIsNone(boards._blockace_detail(listing, NOW))
                get.assert_not_called()

    def test_detail_timeout_is_reported_and_keeps_unconfirmed_lead(self):
        def fetch_example():
            return [boards._blockace_detail(job(), NOW)]
        with patch('boards._get', side_effect=httpx.ReadTimeout('timeout')):
            rows = source_health.run_source(fetch_example)
        self.assertEqual('', rows[0].posted)
        self.assertEqual('partial', source_health.snapshot()[0]['status'])
        self.assertEqual(1, source_health.snapshot()[0]['failures'])

    def test_detail_work_is_bounded_and_duplicate_index_cards_do_not_repeat_requests(self):
        cards = ''.join(f'<a href="/jobs/role-{i}"><h3>Growth Manager</h3><p class="Company">Example</p></a>' for i in range(12))
        cards += '<a href="/jobs/role-0"><h3>Growth Manager</h3><p class="Company">Example</p></a>'
        reply = httpx.Response(200, text=cards, request=httpx.Request('GET', 'https://blockace.io'))
        with patch('boards._get', return_value=reply), patch('boards._blockace_detail', side_effect=lambda row, now: row) as detail:
            rows = source_health.run_source(boards.fetch_blockace)
        self.assertEqual(10, detail.call_count)
        self.assertEqual(12, len(rows))
        self.assertIn('bounded_blockace_details_10', source_health.snapshot()[0]['warnings'])


if __name__ == '__main__':
    unittest.main()

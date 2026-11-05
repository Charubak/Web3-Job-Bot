"""Public RSS regressions: no network, credentials, or invented dates."""
import unittest
from unittest.mock import patch

import httpx

import boards
from filters import _is_primary_source
import source_health


def item(title='Growth Marketing Manager', company='Example',
         link='https://cryptojobslist.com/jobs/growth-at-example', date=''):
    return f'''<item><title>{title}</title><link>{link}</link>
    <dc:creator>{company}</dc:creator><media:location>Remote - United States</media:location>
    <description><![CDATA[<p>Own growth strategy and product launches.</p>
    <p>Applicants must reside in the United States.</p>]]></description>{date}</item>'''


def rss(items):
    return f'''<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:media="http://search.yahoo.com/mrss/"><channel><title>CryptoJobsList</title>
    <lastBuildDate>Thu, 05 Nov 2026 06:45:45 GMT</lastBuildDate>{items}</channel></rss>'''.encode()


class CryptoJobsListRSSTests(unittest.TestCase):
    def setUp(self):
        source_health.reset()

    def fetch(self, body=None, status=200):
        reply = httpx.Response(status, content=body if body is not None else rss(item()),
                               request=httpx.Request('GET', boards.CRYPTOJOBSLIST_RSS_URL))
        with patch('boards._get', return_value=reply) as get:
            jobs = source_health.run_source(boards.fetch_cryptojobslist_rss)
        get.assert_called_once_with(boards.CRYPTOJOBSLIST_RSS_URL, follow_redirects=True, timeout=15)
        return jobs

    def test_feed_registered_without_hiding_blocked_category_sources(self):
        self.assertIn(boards.fetch_cryptojobslist_rss, boards.BOARDS)
        self.assertIn(boards.fetch_cryptojobslist, boards.BOARDS)
        self.assertIn(boards.fetch_cryptojobslist_web3, boards.BOARDS)

    def test_company_full_description_and_geographic_restrictions_survive(self):
        job = self.fetch()[0]
        self.assertEqual('Example', job.company)
        self.assertEqual('Growth Marketing Manager', job.title)
        self.assertEqual('Remote - United States', job.location)
        self.assertIn('Applicants must reside in the United States', job.description)
        self.assertFalse(_is_primary_source(job))
        self.assertFalse(job.current_open_verified)
        self.assertEqual('', job.employer_country)

    def test_channel_build_time_never_becomes_a_job_date(self):
        job = self.fetch()[0]
        self.assertEqual('', job.posted)
        self.assertEqual('', job.published_at_upper_bound)
        self.assertIn('rss_original_posting_dates_unverified', source_health.snapshot()[0]['warnings'])

    def test_future_item_dates_are_only_existence_metadata_not_original_dates(self):
        date = '<pubDate>Thu, 05 Nov 2026 06:00:00 GMT</pubDate>'
        job = self.fetch(rss(item(date=date)))[0]
        self.assertEqual('', job.posted)
        self.assertEqual('Thu, 05 Nov 2026 06:00:00 GMT', job.published_at_upper_bound)

    def test_item_count_is_bounded_and_reported(self):
        jobs = self.fetch(rss(''.join(item(link=f'https://cryptojobslist.com/jobs/role-{i}') for i in range(105))))
        self.assertEqual(100, len(jobs))
        self.assertIn('bounded_public_rss_100', source_health.snapshot()[0]['warnings'])

    def test_invalid_or_access_denied_response_is_unavailable_not_zero(self):
        for body, status in [(b'<html>Access denied</html>', 200), (b'Forbidden', 403)]:
            with self.subTest(status=status):
                self.assertEqual([], self.fetch(body, status))
                self.assertEqual('unavailable', source_health.snapshot()[0]['status'])

    def test_empty_valid_feed_is_distinct_from_unavailable(self):
        self.assertEqual([], self.fetch(rss('')))
        self.assertEqual('returned', source_health.snapshot()[0]['status'])
        self.assertEqual('zero_results', source_health.snapshot()[0]['warning'])

    def test_malformed_foreign_and_credential_links_are_not_emitted(self):
        for link in ['https://example.com/jobs/role', 'https://cryptojobslist.com.evil.test/jobs/role',
                     'https://u:secret@cryptojobslist.com/jobs/role', 'javascript:alert(1)',
                     'https://cryptojobslist.com:bad/jobs/role', 'https://cryptojobslist.com/jobs/']:
            with self.subTest(link=link):
                self.assertEqual([], self.fetch(rss(item(link=link))))


if __name__ == '__main__':
    unittest.main()

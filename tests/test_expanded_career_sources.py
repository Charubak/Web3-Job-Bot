"""New verified boards preserve the original age and employer safeguards."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import boards
import direct_sources
from filters import classify_job


class ExpandedCareerSourceTests(unittest.TestCase):
    def test_verified_sources_are_registered_once(self):
        self.assertEqual(1, boards.GREENHOUSE_COMPANIES.count(('primexbt', 'PrimeXBT')))
        self.assertEqual(1, direct_sources.ASHBY_COMPANIES.count(('whetstoneresearch', 'Whetstone Research')))
        self.assertEqual('https://whetstone.cc/', direct_sources.ASHBY_ASSOCIATION_SOURCES['whetstoneresearch'])

    def test_new_greenhouse_board_does_not_refresh_old_requisitions(self):
        payload = {'jobs': [{'title': 'Crypto Growth manager',
            'absolute_url': 'https://job-boards.greenhouse.io/primexbt/jobs/4800228101',
            'location': {'name': 'Remote'}, 'first_published': '2026-03-07T02:41:53-05:00',
            'updated_at': '2026-11-07T00:00:00Z', 'content': 'Own crypto growth strategy and marketing execution.'}]}
        response = httpx.Response(200, json=payload, request=httpx.Request('GET', 'https://boards-api.greenhouse.io'))
        with patch('boards._get', return_value=response):
            job = boards._fetch_greenhouse_one('primexbt', 'PrimeXBT')[0]
        self.assertEqual('2026-03-07T02:41:53-05:00', job.posted)
        self.assertEqual('', job.employer_country)
        self.assertEqual('rejected', classify_job(job, now=datetime(2026, 11, 7, tzinfo=timezone.utc)).status)

    def test_new_ashby_board_last_publication_stays_exclusion_only(self):
        payload = {'jobs': [{'title': 'Marketing Lead', 'isListed': True, 'isRemote': True,
            'jobUrl': 'https://jobs.ashbyhq.com/whetstoneresearch/b80127b6-9644-4737-a292-b4b795ee4eef',
            'location': 'Global', 'publishedAt': '2025-08-25T21:58:54.718+00:00',
            'descriptionHtml': '<p>Own crypto marketing strategy and growth execution.</p>'}]}
        response = httpx.Response(200, json=payload, request=httpx.Request('GET', 'https://api.ashbyhq.com'))
        with patch('boards._get', return_value=response):
            job = direct_sources._fetch_ashby_one('whetstoneresearch', 'Whetstone Research')[0]
        self.assertEqual('', job.posted)
        self.assertEqual('2025-08-25T21:58:54.718+00:00', job.published_at_upper_bound)
        self.assertEqual('', job.employer_country)
        self.assertEqual('rejected', classify_job(job, now=datetime(2026, 11, 7, tzinfo=timezone.utc)).status)


if __name__ == '__main__':
    unittest.main()

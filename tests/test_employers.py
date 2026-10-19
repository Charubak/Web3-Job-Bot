import unittest
from types import SimpleNamespace
from unittest.mock import patch
import employers


class EmployerTests(unittest.TestCase):
    def test_exact_alias_only(self):
        record = {'verification_status': 'verified', 'base_country_code': 'US',
                  'official_source_url': 'https://example.com/about', 'checked_at': '2026-10-19'}
        jobs = [SimpleNamespace(company=' Example '), SimpleNamespace(company='Example India subsidiary')]
        with patch('employers._registry', return_value={'example': record}):
            employers.enrich_employers(jobs)
        self.assertEqual(jobs[0].employer_country, 'US')
        self.assertEqual(jobs[1].employer_country, '')

    def test_unknown_record_does_not_auto_allow(self):
        jobs = [SimpleNamespace(company='Example')]
        with patch('employers._registry', return_value={'example': {'verification_status': 'unknown'}}):
            employers.enrich_employers(jobs)
        self.assertEqual(jobs[0].employer_country, '')

    def test_known_foreign_employer_india_office_is_not_india_base(self):
        jobs = [SimpleNamespace(company='BitGo', location='India')]
        employers.enrich_employers(jobs)
        self.assertEqual(jobs[0].employer_country, 'US')
        self.assertTrue(jobs[0].employer_country_source.startswith('https://www.bitgo.com/'))

    def test_verified_entries_have_sources(self):
        for entry in employers._registry().values():
            if entry['verification_status'] == 'verified':
                self.assertTrue(entry['official_source_url'].startswith('https://'))
                self.assertTrue(entry['checked_at'])
                self.assertTrue(entry['evidence'])


if __name__ == '__main__':
    unittest.main()

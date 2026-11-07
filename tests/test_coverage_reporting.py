"""Coverage limits must survive the compact manifest-to-Markdown path."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from research_collector import build_report, markdown


class CoverageReportingTests(unittest.TestCase):
    def build(self, sources):
        with patch('research_collector.enrich_employers', side_effect=lambda jobs: jobs):
            return build_report([], {}, sources, now=datetime(2026, 11, 7, tzinfo=timezone.utc))[0]

    def source(self, **changes):
        row = dict(source='example', status='returned', count=0, failures=0,
                   warning='', warnings=[], requests=[])
        row.update(changes)
        return row

    def test_all_adapter_warnings_survive_compact_markdown(self):
        report = self.build([self.source(source='rss', count=100, warnings=[
            'bounded_public_rss_100', 'rss_original_posting_dates_unverified'])])
        text = markdown(report)
        self.assertIn('Bounded sources: rss', text)
        self.assertIn('bounded_public_rss_100', text)
        self.assertIn('rss_original_posting_dates_unverified', text)
        self.assertEqual(['rss'], report['coverage']['bounded_sources'])

    def test_access_failures_are_not_successful_empty_searches(self):
        report = self.build([self.source(source='blocked', status='unavailable', failures=1),
                             self.source(source='empty', warning='zero_results')])
        self.assertEqual(['blocked'], report['coverage']['failed_or_partial_sources'])
        self.assertEqual(['empty'], report['coverage']['successful_zero_result_sources'])
        # Preserve the original schema field for existing machine consumers.
        self.assertEqual(['blocked', 'empty'], report['coverage']['zero_result_sources'])
        text = markdown(report)
        self.assertIn('Incomplete sources: blocked', text)
        self.assertIn('Successful requests with no listings (not proof of no jobs): empty', text)

    def test_bounds_do_not_claim_source_failed_or_coverage_complete(self):
        report = self.build([self.source(count=10, warnings=['bounded_pagination'])])
        self.assertEqual([], report['coverage']['failed_or_partial_sources'])
        self.assertFalse(report['coverage']['complete'])
        self.assertEqual('returned', report['sources'][0]['status'])

    def test_empty_and_repeated_warnings_render_cleanly(self):
        text = markdown(self.build([self.source(warning='zero_results', warnings=['', 'zero_results'])]))
        row = next(line for line in text.splitlines() if line.startswith('- example:'))
        self.assertEqual(1, row.count('zero_results'))
        self.assertFalse(row.endswith(';'))

    def test_non_bound_warnings_do_not_become_cap_claims(self):
        report = self.build([self.source(warnings=['expired_blockace_listing_suppressed'])])
        self.assertEqual([], report['coverage']['bounded_sources'])
        self.assertIn('expired_blockace_listing_suppressed', markdown(report))

    def test_research_followups_handle_caps_and_no_remote_variants(self):
        report = self.build([])
        followups = {row['source']: row['query'] for row in report['search_followups']}
        self.assertIn('without remote', followups['LinkedIn posts/jobs'])
        self.assertIn('supported date window', followups['Bounded sources'])
        self.assertIn('Stop at access restrictions', followups['Bounded sources'])


if __name__ == '__main__':
    unittest.main()

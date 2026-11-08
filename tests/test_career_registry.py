import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import boards
import career_registry as registry
from filters import _is_primary_source
from research_collector import canonical_history, build_report, identity
import source_health

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)


def company(slug='example'):
    url = 'https://jobs.ashbyhq.com/' + slug
    return {'name': slug, 'company_url': 'https://example.com', 'careers_url': url,
            'review': {'status': 'verified_association', 'careers_url': url,
                       'company_name': 'Example', 'official_source_url': 'https://example.com/careers',
                       'checked_at': '2026-11-08'}}


class CareerRegistryTests(unittest.TestCase):
    def test_pinned_discovery_provenance_and_license(self):
        data = registry.load_registry()
        self.assertEqual(194, len(data['companies']))
        self.assertEqual('1b5e24677f81c91080bd2afc5d7bfd300eeaeb46', data['provenance']['blob_sha'])
        self.assertEqual('Apache-2.0', data['provenance']['license'])
        self.assertTrue((registry.REGISTRY_PATH.parent / data['provenance']['license_file']).is_file())
        audit = registry.registry_audit(now=NOW)
        self.assertEqual(9, audit['counts']['active_additional'])
        self.assertTrue(all(row['adapter'] == 'ashby' for row in audit['active']))

    def test_only_exact_https_board_urls(self):
        bad = ['http://jobs.ashbyhq.com/example', 'https://jobs.ashbyhq.com.evil.test/example',
               'https://user@jobs.ashbyhq.com/example', 'https://jobs.ashbyhq.com:8443/example',
               'https://jobs.ashbyhq.com/example/job-id', 'https://jobs.ashbyhq.com/%2e%2e',
               'https://jobs.ashbyhq.com/example?token=x', 'https://jobs.ashbyhq.com/example#jobs',
               'https://jobs.ashbyhq.com/example\\other', 'https://localhost/example']
        for url in bad:
            with self.subTest(url=url):
                self.assertIsNone(registry.board_key(url))
        self.assertEqual(('ashby', 'Case.Sensitive'), registry.board_key('https://jobs.ashbyhq.com/Case.Sensitive'))

    def test_unreviewed_cannot_fetch_and_curated_wins(self):
        unreviewed = company('unreviewed'); unreviewed.pop('review')
        curated = company('existing')
        data = {'companies': [unreviewed, curated, company('new')]}
        with patch('career_registry.curated_keys', return_value={('ashby', 'existing')}):
            audit = registry.registry_audit(data, now=NOW)
        self.assertEqual(['new'], [r['slug'] for r in audit['active']])
        self.assertEqual(1, audit['counts']['requires_association_review'])
        self.assertEqual(1, audit['counts']['already_curated'])

    def test_changed_url_wrong_domain_stale_or_future_review_stays_passive(self):
        cases = []
        for change in ({'careers_url': 'https://jobs.ashbyhq.com/different'},
                       {'official_source_url': 'https://other.test/careers'},
                       {'official_source_url': 'https://person@example.com/careers'},
                       {'checked_at': '2026-11-09'}, {'checked_at': '2026-01-01'},
                       {'checked_at': 'not-a-date'}, {'company_name': ''}):
            row = company(); row['review'].update(change); cases.append(row)
        for row in cases:
            self.assertFalse(registry.reviewed(row, NOW))

    def test_duplicates_and_budget(self):
        data = {'companies': [company(str(i)) for i in range(15)] + [company('0')]}
        audit = registry.registry_audit(data, now=NOW)
        self.assertEqual(12, len(audit['active']))
        self.assertTrue(audit['bounded'])
        self.assertEqual(1, audit['counts']['duplicate_board'])

    def test_runtime_uses_only_selected_adapters_and_exposes_cap(self):
        audit = registry.registry_audit({'companies': [company()]}, now=NOW)
        audit['bounded'] = True
        source_health.reset()
        with patch('career_registry.registry_audit', return_value=audit), \
             patch('direct_sources._fetch_ashby_one', return_value=[]) as fetch:
            self.assertEqual([], source_health.run_source(registry.fetch_registry_careers))
        fetch.assert_called_once_with('example', 'Example')
        self.assertIn('bounded_registry_boards', source_health.snapshot()[0]['warnings'])

    def test_unsupported_upstream_urls_do_not_become_network_targets(self):
        row = company(); row['careers_url'] = 'https://internal.test/jobs'
        row['review']['careers_url'] = row['careers_url']
        audit = registry.registry_audit({'companies': [row]}, now=NOW)
        self.assertEqual([], audit['active'])

    def test_all_sources_wired_and_restricted_source_absent(self):
        names = {fn.__name__ for fn in boards.BOARDS}
        self.assertTrue({'fetch_registry_careers', 'fetch_workable', 'fetch_teamtailor'} <= names)
        self.assertFalse(any('himalaya' in name for name in names))
        for source, expected in [('workable/walletconnect', True), ('workable/unreviewed', False),
                                 ('teamtailor/crossmint.na.teamtailor.com', True), ('teamtailor/unreviewed', False)]:
            self.assertEqual(expected, _is_primary_source(boards.Job('id', 'role', 'company', '', '', source)))

    def test_old_hash_history_survives_canonical_alias(self):
        old_url = 'https://crossmint.na.teamtailor.com/jobs/123-old-title'
        new_url = 'https://crossmint.na.teamtailor.com/jobs/123-new-title'
        old_key = hashlib.sha256(old_url.encode()).hexdigest()[:16]
        previous = {old_key: {'url': old_url, 'first_seen_at': '2026-09-01T00:00:00Z',
                             'first_posted_at': '2026-09-01T00:00:00Z', 'expired_at': '2026-10-10T00:00:00Z'}}
        job = boards.Job('new', 'Growth Marketing Manager', 'Crossmint', 'Remote', new_url,
                         'teamtailor/crossmint.na.teamtailor.com', posted='2026-11-07',
                         description='Own growth marketing in a web3 company.', current_open_verified=True)
        frozen = copy.deepcopy(previous)
        aliases = canonical_history(previous, now=NOW)
        self.assertIn(old_key, aliases)
        self.assertEqual(previous[old_key]['expired_at'], aliases[identity(job)]['expired_at'])
        report, history = build_report([job], previous, [], now=NOW)
        self.assertEqual([], report['candidates'])
        self.assertEqual('rejected', report['rejected_candidates'][0]['classification'])
        self.assertEqual(previous, frozen)

    def test_history_does_not_merge_different_requisitions(self):
        rows = {str(i): {'url': f'https://apply.workable.com/company/j/ID{i}/',
                         'first_seen_at': '2026-11-01T00:00:00Z'} for i in range(2)}
        aliases = canonical_history(rows, now=NOW)
        self.assertEqual(4, len(aliases))

    def test_failed_registry_audit_does_not_abort_other_sources(self):
        with patch('career_registry.registry_audit', side_effect=ValueError('Malformed registry')):
            report, history = build_report([], {}, [], now=NOW)
        self.assertEqual('unavailable', report['career_registry']['status'])
        self.assertEqual([], report['candidates'])


if __name__ == '__main__':
    unittest.main()

"""Synthetic visible-DOM fixtures; no live network or application submissions.

The text/heading labels were checked against the public page on 2026-10-19.
Live HTTP DOM validation is separate; these fixtures do not claim to be a raw
Getro HTML capture.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import boards
import employers
import hylo_source as hylo


ROLE = '''<html><head><title>Marketing &amp; Growth Lead @ Hylo</title></head>
<body><nav>Search jobs</nav><main>
<h2>Marketing &amp; Growth Lead</h2><div>Hylo</div>
<div>Marketing &amp; Communications · Contractor</div><div>Remote</div>
<div>Posted on Oct 15, 2026</div><button>Apply now</button>
<h2>The Role</h2><p>Hylo seeks a senior marketing and growth leader to direct
strategy and campaigns, work with founders, build adoption, and measure acquisition.</p>
<h2>About Hylo</h2><p>This Solana DeFi protocol develops leveraged tokens including
xSOL and dollar products including hyUSD. It focuses on usable financial products.</p>
<h2>What You'll Do</h2><ul><li>Own GTM, launch plans, distribution and marketing campaigns.</li>
<li>Develop creative content and build partner relationships.</li><li>Experiment with
acquisition channels, measure conversion and manage resources.</li></ul>
<h2>What You Bring</h2><p>Experience in Solana DeFi, strong writing skills,
independent execution and a measurable track record of growth marketing outcomes.</p>
<button>Apply now</button></main><footer>Other jobs</footer></body></html>'''


def response(text=ROLE, status=200, url=hylo.HYLO_JOB_URL):
    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


class HyloSourceTests(unittest.TestCase):
    def fetch(self, page=ROLE, **kwargs):
        with patch("boards._get", return_value=response(page, **kwargs)):
            return hylo.fetch_hylo()

    def test_one_bounded_request_exact_identity_and_live_date(self):
        with patch("boards._get", return_value=response()) as get:
            job = hylo.fetch_hylo()[0]
        get.assert_called_once_with(hylo.HYLO_JOB_URL, follow_redirects=True, timeout=12)
        self.assertEqual(job.title, "Marketing & Growth Lead")
        self.assertEqual(job.company, "Hylo")
        self.assertEqual(job.source, "ecosystem/solana/hylo")
        self.assertEqual(job.id, boards._make_id(hylo.HYLO_JOB_URL))
        self.assertEqual(job.url, hylo.HYLO_JOB_URL)
        self.assertEqual(job.posted, "2026-10-15")
        self.assertEqual(job.location, "Remote")
        self.assertEqual(job.salary, "")
        self.assertEqual(job.employer_country, "")
        self.assertIn("Contractor", job.description)
        self.assertEqual(job.published_at_upper_bound, "2026-10-15")
        self.assertTrue(job.current_open_verified)

    def test_full_description_and_separate_header_restrictions_survive(self):
        page = ROLE.replace('<div>Remote</div>', '<div>Remote</div><div>United States only</div>')
        page = page.replace('</main>', '<section>Must have US work authorization. '
                            'Must reside in Canada. UTC-5 overlap required.</section></main>')
        job = self.fetch(page)[0]
        self.assertIn("United States only", job.location)
        for value in ("Must have US work authorization", "Must reside in Canada", "UTC-5 overlap required",
                      "What You Bring", "measure conversion"):
            self.assertIn(value, job.description)

    def test_split_date_metadata_does_not_masquerade_as_location(self):
        page=ROLE.replace('Posted on Oct 15, 2026','<span>Posted</span><span>on Oct 15, 2026</span>')
        self.assertEqual(self.fetch(page)[0].location,'Remote')
        page=page.replace('<div>Remote</div>','<div>Remote</div><div>Canada only</div>')
        self.assertIn('Canada only',self.fetch(page)[0].location)

    def test_does_not_invent_worldwide_or_india_eligibility(self):
        job = self.fetch()[0]
        self.assertEqual(job.location, "Remote")
        for invented in ("worldwide", "global remote", "india eligible"):
            self.assertNotIn(invented, job.description.lower())

    def test_unknown_relative_updated_or_invalid_dates_remain_unknown(self):
        for replacement in ("", "Posted yesterday", "Posted on yesterday", "Updated on Oct 19, 2026",
                            "Posted on Oct 32, 2026", "Posted on 10/15/2026"):
            with self.subTest(replacement=replacement):
                job = self.fetch(ROLE.replace("Posted on Oct 15, 2026", replacement))[0]
                self.assertEqual(job.posted, "")
                self.assertEqual(job.published_at_upper_bound, "2026-10-15")

    def test_oldest_explicit_board_date_wins_and_old_bounds_remain(self):
        page = ROLE.replace("Posted on Oct 15, 2026", "Posted on September 1, 2026")
        job = self.fetch(page)[0]
        self.assertEqual(job.posted, "2026-09-01")
        duplicate = ROLE.replace('</main>', '<p>Posted on September 1, 2026</p></main>')
        self.assertEqual(self.fetch(duplicate)[0].posted, "2026-09-01")
        refreshed = self.fetch(ROLE.replace("Oct 15, 2026", "Nov 20, 2026"))[0]
        self.assertEqual(refreshed.posted, "2026-11-20")
        self.assertEqual(refreshed.published_at_upper_bound, "2026-10-15")

    def test_foreign_identity_and_generic_or_truncated_pages_fail_closed(self):
        pages = ["Access denied", '<h2>Marketing &amp; Growth Lead</h2><button>Apply now</button>',
                 ROLE.replace('xSOL', 'community networks'),
                 ROLE.replace('hyUSD', 'collaboration'), ROLE.replace('Solana', 'hylo.com'),
                 ROLE.replace('Marketing &amp; Growth Lead</h2>', 'Community Manager</h2>'),
                 ROLE.replace('<h2>What You Bring</h2>', '<div>What You Bring</div>')]
        for page in pages:
            with self.subTest(page=page[:70]):
                self.assertEqual(self.fetch(page), [])

    def test_explicit_closure_overrides_apply_now(self):
        for closure in ("This role has been filled.", "This job posting is no longer available.",
                        "This position has expired.", "Applications are closed.",
                        "We are no longer accepting applications."):
            with self.subTest(closure=closure):
                self.assertEqual(self.fetch(ROLE.replace('</main>', f'<p>{closure}</p></main>')), [])

    def test_missing_disabled_or_hidden_apply_action_fails_closed(self):
        for button in ('', '<button disabled>Apply now</button>', '<button aria-disabled="true">Apply now</button>',
                       '<button hidden>Apply now</button>', '<button style="display:none">Apply now</button>',
                       '<div hidden><button>Apply now</button></div>', '<a>Apply now</a>',
                       '<a href="javascript:alert(1)">Apply now</a>'):
            with self.subTest(button=button):
                self.assertEqual(self.fetch(ROLE.replace('<button>Apply now</button>', button)), [])

    def test_visible_link_and_nested_button_label_are_supported(self):
        for action in ('<a href="https://example.test/application">Apply now</a>',
                       '<button><span>Apply</span> <span>now</span></button>'):
            self.assertEqual(len(self.fetch(ROLE.replace('<button>Apply now</button>', action))), 1)

    def test_status_errors_and_redirects_do_not_emit_jobs(self):
        for status in (301, 403, 404, 410, 429, 500, 503):
            with self.subTest(status=status):
                self.assertEqual(self.fetch(status=status), [])
        for url in ('https://jobs.solana.com/', hylo.HYLO_COMPANY_URL,
                    hylo.HYLO_JOB_URL.replace('hylo-2', 'hylo'),
                    'https://hylo.com/jobs', hylo.HYLO_JOB_URL.replace('jobs.solana.com', 'jobs.solana.com.evil.test')):
            with self.subTest(url=url):
                self.assertEqual(self.fetch(url=url), [])

    def test_http_timeout_has_no_static_fallback(self):
        with patch('boards._get', side_effect=httpx.ReadTimeout('timed out')) as get:
            self.assertEqual(hylo.fetch_hylo(), [])
        self.assertEqual(get.call_count, 1)

    def test_scripts_cannot_supply_missing_identity_or_fabricated_dates(self):
        page = ROLE.replace('xSOL', '').replace('Posted on Oct 15, 2026', '')
        page += '<script>xSOL Posted on Oct 15, 2026</script>'
        self.assertEqual(self.fetch(page), [])

    def test_employer_evidence_is_exact_crypto_identity_and_company_base(self):
        registry = json.loads(Path(employers.REGISTRY_PATH).read_text())
        entry = next(x for x in registry['employers'] if x['employer_name'] == 'Hylo')
        self.assertEqual(entry['base_country_code'], 'US')
        self.assertEqual(entry['evidence_type'], 'investor_stated_company_base')
        self.assertEqual(entry['checked_at'], '2026-10-19')
        self.assertEqual(entry['official_source_url'], 'https://colosseum.com/companies/hylo')
        self.assertNotIn('hylo.com', entry['aliases'])
        self.assertIn('not a verified legal headquarters', entry['notes'])
        jobs = [self.fetch()[0], boards.Job('other', 'Growth Lead', 'hylo.com', 'Remote', '', '')]
        employers._registry.cache_clear()
        employers.enrich_employers(jobs)
        self.assertEqual(jobs[0].employer_country, 'US')
        self.assertEqual(jobs[1].employer_country, '')
        self.assertEqual(registry['summary']['total'], len(registry['employers']))


if __name__ == '__main__':
    unittest.main()

"""Offline fixtures for verified direct sources; never contact external services."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

import boards
import direct_sources as direct
from filters import classify_job

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def response(payload=None, text="", status=200, url=direct.GAINS_JOB_URL):
    kwargs = {"json": payload} if payload is not None else {"text": text}
    return httpx.Response(status, request=httpx.Request("GET", url), **kwargs)


def ashby_posting(**changes):
    posting = {
        "id": "role-123", "title": "Senior Growth Marketing Manager",
        "jobUrl": "https://jobs.ashbyhq.com/trust-wallet/role-123",
        "location": "Remote", "secondaryLocations": [], "isListed": True,
        "isRemote": True, "workplaceType": "Remote", "employmentType": "FullTime",
        "descriptionPlain": "Own growth strategy, GTM and acquisition for our Web3 wallet.",
        "descriptionHtml": "<p>Own growth strategy, GTM and acquisition for our Web3 wallet.</p>",
        "publishedAt": "2026-10-01T10:00:00Z",
    }
    posting.update(changes)
    return posting


GAINS_INDEX = '''<html><body><h1>Gains is hiring.</h1>
<a class="role" href="/roles/head-of-growth/">
<div class="role-name">Chief Marketing Officer / Head of Growth</div>
<div class="role-meta">Growth</div><div class="role-meta">Remote</div>
<div class="role-meta">Full time</div></a></body></html>'''

# Reduced fixture preserving the live page's structure (checked 2026-10-19).
GAINS_ROLE = '''<html><head><title>Head of Growth at Gains</title></head><body>
<nav>Hiring</nav><header class="hero"><div class="eyebrow">
Open role · Growth · Remote · Full time · Reports to the Project Leads</div>
<h1 class="title">Chief Marketing Officer<br><span>Head of Growth.</span></h1>
<p>We are looking for a crypto-native growth operator.</p></header>
<section id="company">Gains runs a perpetuals exchange in DeFi.</section>
<section id="mandate">You own global distribution: strategy, budget and channels.
You build the system yourself first, then hire the team.</section>
<section id="skills">Fluent English and product marketing experience.</section>
<section id="how"><ul><li>Remote, international, fast.</li></ul></section>
<section id="apply"><form id="applyForm" data-role="CMO / Head of Growth">
<input name="name" required><input name="telegram" required>
<button id="applySubmit" type="submit">Send application</button></form>
<div hidden id="applyDone">Received</div></section>
<footer>© 2026 GAINS</footer></body></html>'''


class AshbySourceTests(unittest.TestCase):
    def fetch(self, posting):
        with patch("boards._get", return_value=response({"jobs": [posting]})):
            return direct._fetch_ashby_one("trust-wallet", "Trust Wallet")

    def test_uses_shared_bounded_public_fetch_and_preserves_identity(self):
        with patch("boards._get", return_value=response({"jobs": [ashby_posting()]})) as get:
            posting = direct._fetch_ashby_one("trust-wallet", "Trust Wallet")[0]
        get.assert_called_once_with(
            "https://api.ashbyhq.com/posting-api/job-board/trust-wallet",
            params={"includeCompensation": "true"}, timeout=12,
        )
        self.assertEqual(posting.source, "ashby/trust-wallet")
        self.assertEqual(posting.id, boards._make_id(posting.url))
        self.assertEqual(posting.company, "Trust Wallet")
        self.assertEqual(posting.employer_country, "")

    def test_last_published_and_updated_never_become_original_date(self):
        for date in ("2024-01-01T00:00:00Z", "2026-10-01T10:00:00Z", "", None):
            with self.subTest(date=date):
                posting = self.fetch(ashby_posting(publishedAt=date,
                    updatedAt="2026-10-19T00:00:00Z", createdAt="2026-10-01T00:00:00Z"))[0]
                self.assertEqual(posting.posted, "")
                self.assertEqual(posting.published_at_upper_bound, date or "")
                if date:
                    self.assertIn(date, posting.description)
                    self.assertIn("Original posting date unverified", posting.description)

    def test_old_last_publication_excludes_but_current_open_unknown_age_is_allowed(self):
        old = self.fetch(ashby_posting(location="Remote - Global", publishedAt="2026-08-27T12:59:24Z"))[0]
        self.assertEqual(old.posted, "")
        self.assertEqual(classify_job(old, now=NOW).status, "rejected")
        recent = self.fetch(ashby_posting(location="Remote - Global", publishedAt="2026-10-01T10:00:00Z"))[0]
        recent.employer_country = "US"
        recent.employer_country_source = "https://example.test/about"
        recent.employer_country_verified_at = "2026-10-19"
        decision = classify_job(recent, now=NOW)
        self.assertEqual(decision.status, "qualified")
        self.assertEqual(recent.posted, "")
        self.assertTrue(any("date" in reason.lower() for reason in decision.reasons))

    def test_all_primary_secondary_addresses_and_workplace_survive(self):
        posting = self.fetch(ashby_posting(
            address={"postalAddress": {"addressCountry": "USA"}},
            secondaryLocations=[
                {"location": "New York", "address": {"postalAddress": {
                    "addressLocality": "New York", "addressRegion": "NY", "addressCountry": "USA"}}},
                {"location": "Canada", "address": {"addressLocality": "Toronto", "addressCountry": "Canada"}},
            ], workplaceType="Hybrid",
        ))[0]
        for value in ("Remote", "USA", "New York", "NY", "Canada", "Toronto", "Hybrid"):
            self.assertIn(value, posting.location)
        self.assertEqual(classify_job(posting, now=NOW).status, "rejected")

    def test_remote_boolean_does_not_invent_worldwide_or_india_eligibility(self):
        posting = self.fetch(ashby_posting(location="", workplaceType=None))[0]
        self.assertEqual(posting.location, "Remote")
        self.assertNotIn("worldwide", posting.description.lower())
        self.assertEqual(classify_job(posting, now=NOW).status, "review")
        unknown = self.fetch(ashby_posting(location="", workplaceType=None, isRemote=None))[0]
        self.assertEqual(unknown.location, "")

    def test_explicit_false_remote_and_hybrid_override_remote_marketing_text(self):
        for changed in ({"isRemote": False}, {"workplaceType": "OnSite"}, {"workplaceType": "Hybrid"}):
            with self.subTest(changed=changed):
                posting = self.fetch(ashby_posting(location="Remote - Global", **changed))[0]
                self.assertEqual(classify_job(posting, now=NOW).status, "rejected")

    def test_restrictions_in_either_description_are_preserved(self):
        posting = self.fetch(ashby_posting(
            descriptionPlain="Own global growth. Must reside in Canada.",
            descriptionHtml="<h2>Requirements</h2><p>Must have US work authorization.</p>",
        ))[0]
        self.assertIn("Must reside in Canada", posting.description)
        self.assertIn("Must have US work authorization", posting.description)
        self.assertIn("Employment type: FullTime", posting.description)

    def test_optional_compensation_is_preserved(self):
        posting = self.fetch(ashby_posting(compensation={
            "compensationTierSummary": "$120K – $180K • Equity"}))[0]
        self.assertEqual(posting.salary, "$120K – $180K • Equity")

    def test_unlisted_closed_and_incomplete_rows_are_suppressed(self):
        changes = [
            {"isListed": False}, {"isListed": None}, {"isOpen": False},
            {"isArchived": True}, {"status": "Closed"}, {"status": "Draft"},
            {"title": ""}, {"descriptionPlain": "", "descriptionHtml": ""},
            {"descriptionPlain": "This role has been filled."},
            {"descriptionPlain": "This job posting is no longer available."},
            {"descriptionPlain": "This position has expired."},
            {"descriptionHtml": "<p>We are no longer accepting applications.</p>"},
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                self.assertEqual(self.fetch(ashby_posting(**changed)), [])

    def test_unverified_or_malformed_job_urls_are_suppressed(self):
        for url in ("", "javascript:alert(1)", "https://example.test/job/123",
                    "https://jobs.ashbyhq.com/other/123", "https://jobs.ashbyhq.com/trust-wallet",
                    "https://jobs.ashbyhq.com.evil.test/trust-wallet/123",
                    "https://attacker@jobs.ashbyhq.com/trust-wallet/123",
                    "https://jobs.ashbyhq.com/trust-wallet/../123",
                    "https://jobs.ashbyhq.com:bad/trust-wallet/123"):
            with self.subTest(url=url):
                self.assertEqual(self.fetch(ashby_posting(jobUrl=url)), [])

    def test_errors_and_generic_200_payloads_are_not_open_jobs(self):
        for reply in (response(status=404), response(status=503), response(text="Access denied"),
                      response({"error": "not found"}), response({"jobs": "invalid"})):
            with self.subTest(reply=reply):
                with patch("boards._get", return_value=reply):
                    self.assertEqual(direct._fetch_ashby_one("trust-wallet", "Trust Wallet"), [])

    def test_companies_are_fetched_sequentially_and_one_failure_does_not_stop_others(self):
        with patch("direct_sources.ASHBY_COMPANIES", [("trust-wallet", "Trust Wallet"), ("allium", "Allium")]), \
                patch("boards._get", side_effect=[response(status=503), response({"jobs": [ashby_posting(
                    jobUrl="https://jobs.ashbyhq.com/allium/role-123")]})]) as get, \
                patch("concurrent.futures.ThreadPoolExecutor", side_effect=AssertionError("nested pool")):
            jobs = direct.fetch_ashby()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Allium")
        self.assertEqual(get.call_count, 2)

    def test_registry_has_official_source_for_every_board(self):
        self.assertEqual({slug for slug, _ in direct.ASHBY_COMPANIES}, set(direct.ASHBY_ASSOCIATION_SOURCES))
        self.assertEqual(len({slug for slug, _ in direct.ASHBY_COMPANIES}), len(direct.ASHBY_COMPANIES))
        for source in direct.ASHBY_ASSOCIATION_SOURCES.values():
            self.assertTrue(source.startswith("https://"))


class GainsSourceTests(unittest.TestCase):
    def fetch(self, page=GAINS_ROLE, index=GAINS_INDEX):
        with patch("boards._get", side_effect=[
            response(text=index, url=direct.GAINS_INDEX_URL), response(text=page),
        ]):
            return direct.fetch_gains()

    def test_actual_title_open_form_full_body_and_unknown_metadata(self):
        posting = self.fetch()[0]
        self.assertEqual(posting.title, "Chief Marketing Officer / Head of Growth")
        self.assertEqual(posting.company, "Gains")
        self.assertEqual(posting.url, direct.GAINS_JOB_URL)
        self.assertEqual(posting.source, "company/gains.trade")
        self.assertEqual(posting.posted, "")
        self.assertEqual(posting.employer_country, "")
        self.assertIn("Remote", posting.location)
        self.assertIn("Full time", posting.location)
        self.assertIn("Remote, international, fast", posting.description)
        self.assertIn("Fluent English", posting.description)
        self.assertIn("You build the system yourself first", posting.description)
        self.assertNotIn("Received", posting.description)
        self.assertNotIn("worldwide", posting.description.lower())
        self.assertEqual(classify_job(posting, now=NOW).status, "review")

    def test_only_two_bounded_gets_and_no_application_submission(self):
        with patch("boards._get", side_effect=[response(text=GAINS_INDEX, url=direct.GAINS_INDEX_URL),
                                               response(text=GAINS_ROLE)]) as get:
            self.assertEqual(len(direct.fetch_gains()), 1)
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         [direct.GAINS_INDEX_URL, direct.GAINS_JOB_URL])
        self.assertTrue(all(call.kwargs == {"follow_redirects": True, "timeout": 12}
                            for call in get.call_args_list))

    def test_nested_hidden_decoration_does_not_break_visible_role(self):
        page = GAINS_ROLE.replace("<nav>Hiring</nav>", '<nav>Hiring</nav>'
                                 '<div style="display:none"><span style="color:red">Decoration</span></div>')
        posting = self.fetch(page)[0]
        self.assertNotIn("Decoration", posting.description)

    def test_new_geographic_restrictions_survive_full_body_and_metadata(self):
        for page in (
            GAINS_ROLE.replace("Remote · Full time", "Remote · Canada only · Full time"),
            GAINS_ROLE.replace('<section id="how">',
                               '<section id="eligibility">Must reside in Canada.</section><section id="how">'),
        ):
            with self.subTest(page=page):
                posting = self.fetch(page)[0]
                self.assertIn("Canada", posting.location + posting.description)
                self.assertEqual(classify_job(posting, now=NOW).status, "rejected")

    def test_missing_or_closed_index_prevents_stale_detail_fetch(self):
        for index in ("<h1>Not found</h1>", GAINS_INDEX.replace("class=\"role\"", "class=\"archived\""),
                      GAINS_INDEX.replace("Full time", "This role is closed"),
                      GAINS_INDEX.replace("/roles/head-of-growth/", "/open-application/")):
            with self.subTest(index=index):
                with patch("boards._get", return_value=response(text=index, url=direct.GAINS_INDEX_URL)) as get:
                    self.assertEqual(direct.fetch_gains(), [])
                self.assertEqual(get.call_count, 1)

    def test_closed_404_generic_200_redirect_and_inactive_form_suppressed(self):
        pages = [
            "<h1>Head of Growth</h1><p>Service temporarily unavailable</p>",
            GAINS_ROLE.replace("Open role", "Closed role"),
            GAINS_ROLE.replace("Open role", "This position has been filled"),
            GAINS_ROLE.replace("Fluent English", "Applications are closed. Fluent English"),
            GAINS_ROLE.replace("type=\"submit\"", "type=\"submit\" disabled"),
            GAINS_ROLE.replace("type=\"submit\"", "type=\"submit\" aria-disabled=\"true\""),
            GAINS_ROLE.replace("<form ", "<form hidden "),
            GAINS_ROLE.replace("<form ", '<form style="display: none" '),
            GAINS_ROLE.replace("name=\"telegram\"", "name=\"unrelated\""),
            GAINS_ROLE.replace("data-role=\"CMO / Head of Growth\"", "data-role=\"Open application\""),
            GAINS_ROLE.replace("id=\"mandate\"", "id=\"unrelated\""),
        ]
        for page in pages:
            with self.subTest(case=pages.index(page)):
                self.assertEqual(self.fetch(page), [])
        for reply in (response(status=404), response(status=410),
                      response(text=GAINS_ROLE, url=direct.GAINS_INDEX_URL),
                      response(text=GAINS_ROLE, url="https://evil.test/roles/head-of-growth/")):
            with self.subTest(reply=reply):
                with patch("boards._get", side_effect=[response(text=GAINS_INDEX, url=direct.GAINS_INDEX_URL), reply]):
                    self.assertEqual(direct.fetch_gains(), [])

    def test_closure_adverbs_override_an_otherwise_active_role(self):
        for closure in (
            "Applications are now closed.",
            "This position has now been filled.",
            "Applications have now been closed.",
            "This role is currently closed.",
            "This job posting has recently been removed.",
            "This position has been filled.",
        ):
            with self.subTest(closure=closure):
                page = GAINS_ROLE.replace("Fluent English", closure + " Fluent English")
                self.assertEqual(self.fetch(page), [])


HACKEN_ROLE = '''<html><body><nav>Careers</nav><main><section>
<div class="styles_headerDescriptionDepartment__abc"><p>Marketing</p><p>Remote</p></div>
<h1>Growth Marketing Manager</h1><p>Hacken is a global leader in blockchain security.</p>
</section><section><ul class="jobDescription"><li>Own the full acquisition funnel and GTM strategy.</li>
<li>Manage the growth budget and run experiments.</li></ul>
<ul class="jobRequirements"><li>5+ years in B2B growth and Web3.</li></ul>
<section>Remote-first Model</section><button type="button">Apply now</button>
</section></main><footer>Company offices and unrelated navigation</footer></body></html>'''


class HackenSourceTests(unittest.TestCase):
    def fetch(self, page=HACKEN_ROLE, status=200, url=direct.HACKEN_JOB_URL):
        with patch("boards._get", return_value=response(text=page, status=status, url=url)):
            return direct.fetch_hacken()

    def test_full_body_remote_metadata_and_unknown_date_hq(self):
        posting = self.fetch()[0]
        self.assertEqual(posting.title, "Growth Marketing Manager")
        self.assertEqual(posting.source, "company/hacken.io")
        self.assertEqual(posting.company, "Hacken")
        self.assertEqual(posting.url, direct.HACKEN_JOB_URL)
        self.assertEqual(posting.posted, "")
        self.assertEqual(posting.employer_country, "")
        self.assertIn("Remote", posting.location)
        self.assertIn("5+ years", posting.description)
        self.assertIn("Manage the growth budget", posting.description)
        self.assertNotIn("unrelated navigation", posting.description)
        self.assertEqual(posting.published_at_upper_bound, "2026-01-06")
        self.assertIn(direct.HACKEN_AGE_EVIDENCE_URL, posting.description)
        self.assertEqual(classify_job(posting, now=NOW).status, "rejected")

    def test_known_old_role_cannot_be_refreshed_by_an_active_apply_button(self):
        posting = self.fetch()[0]
        posting.posted = "2026-10-01T10:00:00Z"
        self.assertEqual(classify_job(posting, now=NOW).status, "rejected")

    def test_shared_bounded_get_and_no_application_request(self):
        with patch("boards._get", return_value=response(text=HACKEN_ROLE, url=direct.HACKEN_JOB_URL)) as get:
            self.assertEqual(len(direct.fetch_hacken()), 1)
        get.assert_called_once_with(direct.HACKEN_JOB_URL, follow_redirects=True, timeout=12)

    def test_new_body_restrictions_are_preserved(self):
        posting = self.fetch(HACKEN_ROLE.replace("5+ years", "Must reside in Canada. 5+ years"))[0]
        self.assertIn("Must reside in Canada", posting.description)
        self.assertEqual(classify_job(posting, now=NOW).status, "rejected")

    def test_missing_closed_disabled_or_mismatched_pages_are_suppressed(self):
        pages = [
            "<main><h1>Growth Marketing Manager</h1><p>Something went wrong.</p></main>",
            HACKEN_ROLE.replace("Apply now", "Position closed"),
            HACKEN_ROLE.replace("<button ", "<button disabled "),
            HACKEN_ROLE.replace("<button ", '<button aria-disabled="true" '),
            HACKEN_ROLE.replace("<button ", "<button hidden "),
            HACKEN_ROLE.replace("<button ", '<button style="visibility: hidden" '),
            HACKEN_ROLE.replace("Remote-first Model", "This role is no longer available."),
            HACKEN_ROLE.replace("Growth Marketing Manager", "Other role"),
            HACKEN_ROLE.replace("jobRequirements", "unrelated"),
            HACKEN_ROLE.replace("jobDescription", "unrelated"),
        ]
        for page in pages:
            with self.subTest(case=pages.index(page)):
                self.assertEqual(self.fetch(page), [])
        for status in (404, 410, 503):
            self.assertEqual(self.fetch(status=status), [])
        self.assertEqual(self.fetch(url="https://hacken.io/careers/"), [])
        self.assertEqual(self.fetch(url="https://hacken.io.evil.test/careers/175289-growth-marketing-manager/"), [])


if __name__ == "__main__":
    unittest.main()

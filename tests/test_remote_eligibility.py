"""Unrestricted Remote is eligible for screening, never guaranteed worldwide."""

import unittest
from datetime import timedelta

from filters import classify_job, filter_jobs
from test_filters import DESCRIPTION, NOW, job


class UnrestrictedRemoteTests(unittest.TestCase):
    def decision(self, **changes):
        fields = {"location": "Remote", "posted": NOW.isoformat()}
        fields.update(changes)
        return classify_job(job(**fields), now=NOW)

    def test_explicit_negation_of_country_only_and_hybrid_rules_is_not_a_restriction(self):
        clause='GetBlock is fully remote by design, with no US-only, EU-only, or UK-only restrictions and no hybrid schedule that puts you in an office three days a week.'
        result=self.decision(description=DESCRIPTION+' '+clause)
        self.assertEqual('qualified',result.status,result.reason)
        result=self.decision(description=DESCRIPTION+' '+clause+' Applicants must reside in Canada.')
        self.assertEqual('rejected',result.status)

    def test_first_party_remote_qualifies_without_worldwide_or_india_claim(self):
        for source in ("lever/example", "greenhouse/example", "ashby/example"):
            with self.subTest(source=source):
                result = self.decision(source=source)
                self.assertEqual("qualified", result.status, result.reason)
                self.assertIn("country eligibility is unconfirmed", result.reason)
                self.assertNotIn("worldwide", result.reason)

    def test_remote_role_description_can_supply_empty_location(self):
        for clause in (
            "This role is fully remote.", "This is a completely remote position.",
            "You will work remotely.", "You can work from home.",
            "Work remotely alongside our product team.", "Workplace type: Remote.",
            "Our position supports remote work.",
        ):
            with self.subTest(clause=clause):
                result = self.decision(location="", description=DESCRIPTION + " " + clause)
                self.assertEqual("qualified", result.status, result.reason)
                self.assertIn("country eligibility is unconfirmed", result.reason)

    def test_remote_title_can_supply_empty_location(self):
        result = self.decision(title="Senior Growth Marketing Manager (Remote)", location="")
        self.assertEqual("qualified", result.status, result.reason)
        self.assertIn("country eligibility is unconfirmed", result.reason)

    def test_no_remote_role_evidence_remains_review(self):
        for clause in (
            "", "We are a distributed team.", "We serve remote communities.",
            "Our globally distributed team collaborates across markets.",
            "Our engineering team works remotely.",
            "Experience marketing remote-work products is useful.",
            "We are a remote-first company.",
        ):
            with self.subTest(clause=clause):
                result = self.decision(location="", description=DESCRIPTION + " " + clause)
                self.assertEqual("review", result.status, result.reason)

    def test_timezone_availability_does_not_become_residence(self):
        for location, clause in (
            ("Remote - US timezone", ""),
            ("Remote - UTC+2 to UTC+8", ""),
            ("Remote", "You must have 4 hours of overlap with US working hours."),
            ("Remote", "Must be based in a European timezone."),
        ):
            with self.subTest(location=location, clause=clause):
                result = self.decision(location=location, description=DESCRIPTION + " " + clause)
                self.assertEqual("qualified", result.status, result.reason)
                self.assertTrue(result.timezone_note)
                self.assertIn("country eligibility is unconfirmed", result.reason)

    def test_contradictory_description_blocks_bare_remote(self):
        for clause in (
            "We only hire in the US.", "This role is remote within EMEA.",
            "Applicants must be based in Europe.",
            "Candidates must live in Canada with US timezone overlap.",
            "Applicants must reside within commuting distance of our office.",
            "Applicants must hold an EU passport.",
            "Applicants must have US work authorization.",
            "This role requires a valid work visa.",
            "Visa sponsorship is not available.",
            "No visa sponsorship is offered.",
            "Eligible locations: Ljubljana.",
            "We only hire in countries where we have an employing entity.",
            "We don't hire worldwide.", "We cannot work from anywhere.",
            "We do not support globally remote work.",
            "Worldwide hiring is not available.",
            "We require 2 days per week in the office.",
            "Mandatory monthly office visits are part of this role.",
            "Office attendance is required.", "This is a hybrid role.",
            "This role is not fully remote.",
        ):
            with self.subTest(clause=clause):
                result = self.decision(description=DESCRIPTION + " " + clause)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_title_and_metadata_restrictions_still_block(self):
        for changes in (
            {"location": "Remote - US"}, {"location": "Remote - EMEA"},
            {"location": "Remote - Bucharest"}, {"location": "Remote (CA)"},
            {"location": "Remote / Hybrid"}, {"location": "Not remote"},
            {"title": "Growth Lead (US Remote)"},
            {"title": "Growth Lead (Hybrid)"}, {"title": "Growth Lead (Not Remote)"},
        ):
            with self.subTest(changes=changes):
                result = self.decision(**changes)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_reviewed_remote_title_qualifiers_fail_closed(self):
        for title in (
            "Growth Lead (Remote, Ljubljana)",
            "Growth Lead (Remote - Manila only)",
            "Growth Lead [Remote - Manila only]",
            "Growth Lead - Remote - Ljubljana",
            "Growth Lead (Ljubljana Remote)",
            "Growth Lead (Partially Remote)",
        ):
            with self.subTest(title=title):
                result = self.decision(title=title)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_reviewed_partial_remote_and_unknown_restrictions_fail_closed(self):
        for clause in (
            "This position is partially remote.",
            "This role is partly remote.",
            "This is a semi-remote role.",
            "Remote work is limited to our approved jurisdictions.",
            "Remote work is restricted to selected locations.",
            "Must have the right to work in the country of employment.",
            "Our headquarters is in Singapore. Must have the right to work in the country of employment.",
            "You must be authorized to work in your country of employment.",
            "You must be able to work at our offices when needed.",
            "You must be willing to come to our office when needed.",
            "You are required to be available to attend our offices.",
        ):
            with self.subTest(clause=clause):
                result = self.decision(description=DESCRIPTION + " " + clause)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_parenthesized_qualifier_after_remote_cannot_hide_residence(self):
        for title in (
            "Growth Lead - Remote (must be in Bucharest)",
            "Growth Lead - Remote (Ljubljana)",
            "Growth Lead - Remote [must be in Bucharest]",
        ):
            for location in ("", "Remote", "Remote - Worldwide"):
                with self.subTest(title=title, location=location):
                    result = self.decision(title=title, location=location)
                    self.assertEqual("review", result.status, result.reason)

    def test_neutral_title_remote_qualifiers_still_qualify(self):
        for title in (
            "Growth Lead (Remote)", "Growth Lead (Remote - Worldwide)",
            "Growth Lead [Remote - UTC+2 to UTC+8]",
            "Growth Lead - Remote - Flexible", "Growth Lead (Fully Remote)",
            "Growth Lead - Remote (worldwide)", "Growth Lead - Remote (UTC+2)",
        ):
            with self.subTest(title=title):
                result = self.decision(title=title)
                self.assertEqual("qualified", result.status, result.reason)

    def test_other_evidence_requirements_are_unchanged(self):
        for changes in (
            {"source": "web3.career"}, {"source": "x.com"},
            {"source": "lever/"}, {"description": "Remote growth role."},
            {"description": ""}, {"employer_country": ""},
            {"employer_country_source": ""}, {"employer_country_verified_at": ""},
            {"employer_country_verified_at": "2026-01-01"}, {"url": ""},
            {"posted": ""}, {"posted": (NOW + timedelta(seconds=1)).isoformat()},
        ):
            with self.subTest(changes=changes):
                result = self.decision(**changes)
                self.assertEqual("review", result.status, result.reason)

    def test_non_target_and_india_based_roles_still_reject(self):
        for changes in (
            {"employer_country": "India"}, {"title": "Business Development Lead"},
            {"title": "Community Growth Lead"}, {"title": "Senior Event Marketing Manager"},
        ):
            with self.subTest(changes=changes):
                result = self.decision(**changes)
                self.assertEqual("rejected", result.status, result.reason)

    def test_remote_age_window_has_no_minimum(self):
        for days in (0, 1, 3, 13, 14, 30):
            with self.subTest(days=days):
                result = self.decision(posted=(NOW - timedelta(days=days)).isoformat())
                self.assertEqual("qualified", result.status, result.reason)
        result = self.decision(posted=(NOW - timedelta(days=30, seconds=1)).isoformat())
        self.assertEqual("rejected", result.status, result.reason)

    def test_verified_open_undated_role_qualifies_with_date_uncertainty(self):
        for posted in ("", None, "Unknown", "unparseable"):
            with self.subTest(posted=posted):
                result = self.decision(posted=posted, current_open_verified=True)
                self.assertEqual("qualified", result.status, result.reason)
                self.assertIn("original posting date is unconfirmed", result.reason)
                self.assertEqual(posted, result.job.posted)

    def test_undated_role_requires_explicit_verified_open_flag(self):
        for verified in (False, None, "true", 1):
            with self.subTest(verified=verified):
                result = self.decision(posted="", current_open_verified=verified)
                self.assertEqual("review", result.status, result.reason)

    def test_current_open_flag_cannot_bypass_source_or_other_evidence(self):
        for changes in ({"source": "web3.career"}, {"description": ""}, {"employer_country": ""}):
            with self.subTest(changes=changes):
                result = self.decision(posted="", current_open_verified=True, **changes)
                self.assertEqual("review", result.status, result.reason)

    def test_old_original_or_publication_bound_still_rejects_verified_open(self):
        old = (NOW - timedelta(days=31)).isoformat()
        for changes in (
            {"posted": old},
            {"posted": "", "published_at_upper_bound": old},
            {"posted": NOW.isoformat(), "published_at_upper_bound": old},
        ):
            with self.subTest(changes=changes):
                result = self.decision(current_open_verified=True, **changes)
                self.assertEqual("rejected", result.status, result.reason)

    def test_future_date_still_reviews_verified_open_role(self):
        result = self.decision(current_open_verified=True, posted=(NOW + timedelta(seconds=1)).isoformat())
        self.assertEqual("review", result.status, result.reason)

    def test_recent_republication_does_not_supply_an_original_date(self):
        result = self.decision(posted="", published_at_upper_bound=NOW.isoformat(), current_open_verified=True)
        self.assertEqual("qualified", result.status, result.reason)
        self.assertEqual("", result.job.posted)
        self.assertIn("original posting date is unconfirmed", result.reason)

    def test_verified_open_undated_jobs_sort_after_dated_jobs(self):
        undated = job(id="undated", location="Remote", posted="", current_open_verified=True)
        dated = job(id="dated", location="Remote", posted=NOW.isoformat())
        results = filter_jobs([undated, dated], now=NOW)
        self.assertEqual([dated, undated], results.qualified)

    def test_explicit_worldwide_advertisement_stays_distinct_from_bare_remote(self):
        result = self.decision(location="Remote - Worldwide")
        self.assertEqual("qualified", result.status, result.reason)
        self.assertIn("explicitly advertises worldwide remote", result.reason)
        self.assertNotIn("guaranteed", result.reason)

    def test_non_restrictions_do_not_block_remote(self):
        for clause in (
            "No visa is required.", "You are not required to be based in the US.",
            "No office attendance is required.",
            "Our company is headquartered in Singapore and has an office in India.",
        ):
            with self.subTest(clause=clause):
                result = self.decision(description=DESCRIPTION + " " + clause)
                self.assertEqual("qualified", result.status, result.reason)


if __name__ == "__main__":
    unittest.main()

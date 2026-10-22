"""Offline regression tests for precise, evidence-based job screening."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from filters import (
    FilterDecision,
    _parse_posted_date,
    apply_filters,
    classify_job,
    filter_jobs,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
DESCRIPTION = (
    "Own growth strategy, acquisition and retention for our protocol. "
    "Build experiments with product and analytics, define messaging and lead "
    "cross-functional product launches. We need an experienced hands-on marketer "
    "with 6+ years of marketing experience."
)


def job(**changes):
    fields = dict(
        id="sample-job", title="Senior Growth Marketing Manager", company="Example Labs",
        location="Remote - Worldwide", url="https://jobs.lever.co/example/123",
        source="lever/example", posted="2026-10-05T12:00:00Z", salary="",
        description=DESCRIPTION, employer_country="United Kingdom",
        employer_country_source="https://example.com/about",
        employer_country_verified_at="2026-10-18",
    )
    fields.update(changes)
    return SimpleNamespace(**fields)


class DecisionTests(unittest.TestCase):
    def assert_status(self, expected, **changes):
        result = classify_job(job(**changes), now=NOW)
        self.assertEqual(expected, result.status, result.reason)
        self.assertTrue(result.reasons)
        return result

    def test_dedicated_social_media_managers_are_excluded(self):
        for title in ('Social Media Manager', 'Senior Social Media Manager', 'Social-Media Marketing Manager', 'Manager, Social Media'):
            with self.subTest(title=title):
                self.assert_status('rejected', title=title)

    def test_broad_marketing_can_include_social_partnerships_and_ai(self):
        for title in ('Marketing Manager', 'Marketing Lead', 'Growth Lead', 'Growth Strategist'):
            with self.subTest(title=title):
                self.assert_status('qualified', title=title, description=DESCRIPTION + ' Own social media, outreach, partnership campaigns and AI workflows alongside strategy and execution.')

    def test_strong_primary_roles_qualify(self):
        for title in (
            "Senior Growth Marketing Manager", "Growth Lead", "GTM Lead",
            "Go-to-Market Lead", "Senior Product Marketing Manager", "Senior PMM",
            "Principal Product Marketing Manager", "Marketing Lead", "Growth Manager",
            "Product Marketing Manager", "Growth Marketer", "Growth Specialist",
            "Marketing Specialist",
        ):
            with self.subTest(title=title):
                self.assert_status("qualified", title=title)

    def test_early_stage_hands_on_heads_qualify(self):
        for title in ("Head of Growth", "Head of Marketing", "Director of Growth"):
            with self.subTest(title=title):
                self.assert_status("qualified", title=title, description=DESCRIPTION + " Join our early-stage team as a hands-on player-coach.")

    def test_corporate_head_or_exec_goes_to_review(self):
        for title in ("Head of Growth", "Marketing Director", "VP of Marketing", "Chief Marketing Officer"):
            with self.subTest(title=title):
                self.assert_status("review", title=title)

    def test_thin_or_missing_description_is_review(self):
        for desc in ("", None, "Great growth role. Work from anywhere."):
            with self.subTest(description=desc):
                self.assert_status("review", description=desc)

    def test_unclear_scope_is_review(self):
        for title in ("Senior Content Marketer", "Senior Brand Manager", "Ecosystem Lead"):
            with self.subTest(title=title):
                self.assert_status("review", title=title)

    def test_generic_marketing_without_growth_mandate_is_review(self):
        self.assert_status("review", title="Marketing Lead", description="Lead marketing operations and manage our editorial calendar, coordinate brand consistency and create polished social assets for our globally distributed company.")

    def test_adjacent_specialties_do_not_become_core_roles_from_buzzwords(self):
        for title in ("Senior Content Marketing Manager", "Senior Social Media Marketing Lead", "Brand Marketing Lead", "SEO Marketing Lead"):
            with self.subTest(title=title):
                self.assert_status("review", title=title)

    def test_junior_roles_rejected(self):
        for title in ("Growth Intern", "Marketing Internship", "Junior PMM", "Jr. Growth Manager", "GTM Associate", "Marketing Coordinator", "Marketing Assistant", "Growth Trainee", "Marketing Executive", "Brand Ambassador"):
            with self.subTest(title=title):
                self.assert_status("rejected", title=title)

    def test_junior_description_overrides_inflated_title(self):
        for desc in (
            "This role is an entry-level position.",
            "This is an entry-level role, ideal for fresh graduates.",
            "No previous experience required.",
            "Requirements: 0-2 years of relevant experience.",
            "Requirements: 1 to 3 years of marketing experience.",
            "You have two years of experience.",
        ):
            with self.subTest(description=desc):
                self.assert_status("rejected", description=DESCRIPTION + " " + desc)

    def test_mentoring_juniors_does_not_make_role_junior(self):
        self.assert_status("qualified", description=DESCRIPTION + " You will mentor junior colleagues with 1-2 years of experience.")

    def test_short_domain_tenure_does_not_make_senior_marketer_junior(self):
        self.assert_status("qualified", title="Senior PMM", description="Own product positioning, messaging, and launches. We require 5 years of relevant marketing experience and two years of experience working in crypto.")
        self.assert_status("qualified", description=DESCRIPTION + " You need 1 year of experience in DeFi.")

    def test_low_overall_growth_marketing_experience_is_rejected(self):
        self.assert_status("rejected", description="Own growth strategy and product launches for our startup. We need only 1-2 years of relevant growth marketing experience in this field.")

    def test_non_target_technical_or_sales_titles_rejected(self):
        for title in (
            "Marketing Engineer", "Growth Engineer", "Senior Developer Marketing Lead",
            "GTM Solutions Architect", "Growth Analyst", "Senior DevRel Lead",
            "Product Manager, Growth", "Growth Sales Manager", "Business Development Lead",
            "BD Lead", "Senior Account Executive - Growth", "Customer Success Marketing Lead",
        ):
            with self.subTest(title=title):
                self.assert_status("rejected", title=title)

    def test_technical_pmm_without_engineering_title_can_qualify(self):
        self.assert_status("qualified", title="Senior Technical Product Marketing Manager")

    def test_disguised_quota_carrying_sales_is_rejected(self):
        self.assert_status("rejected", description=DESCRIPTION + " You will carry a sales quota and close deals through outbound prospecting.")

    def test_disguised_community_or_bd_scope_is_rejected(self):
        self.assert_status("rejected", title="Senior Marketing Manager", description="Own community activation across our Discord and Telegram channels. Moderate discussions, grow our ambassador program and coordinate meetups as the core responsibilities of this marketing role. Six years of experience required.")
        self.assert_status("rejected", title="Growth Lead", description="Own acquisition and retention by building partnerships and managing the sales pipeline. Negotiate and execute commercial deals with institutional customers. Source prospects and convert them into clients. Six years of relevant experience.")

    def test_community_bd_and_events_are_excluded(self):
        for title in (
            "Community Moderator", "Head of Community", "Community Growth Lead",
            "Growth and Partnerships Lead", "Marketing Partnerships Lead", "Head of Events",
            "Senior Event Marketing Manager", "Field Marketing Lead",
        ):
            with self.subTest(title=title):
                self.assert_status("rejected", title=title)

    def test_primary_events_or_community_in_description_rejected(self):
        for desc in ("Your primary focus is event marketing and conferences.", "This role focuses on community moderation.", "The main responsibility is business development."):
            with self.subTest(description=desc):
                self.assert_status("rejected", description=DESCRIPTION + " " + desc)

    def test_incidental_events_community_and_sales_collaboration_allowed(self):
        self.assert_status("qualified", description=DESCRIPTION + " Work with sales on positioning, occasionally support community campaigns and attend industry events.")

    def test_boundaries_do_not_match_international_or_business(self):
        self.assert_status("qualified", title="International Growth Lead", description=DESCRIPTION + " Partner with business stakeholders and support international product launches.")

    def test_unknown_locations_without_remote_evidence_are_review(self):
        for location in ("", None, "Distributed", "Global"):
            with self.subTest(location=location):
                self.assert_status("review", location=location)

    def test_unrestricted_primary_remote_locations_qualify_with_uncertainty(self):
        for location in ("Remote", "Remote only", "Fully remote", "Remote-first", "Remote (flexible)"):
            with self.subTest(location=location):
                result = self.assert_status("qualified", location=location)
                self.assertIn("country eligibility is unconfirmed", result.reason)
                self.assertNotIn("explicitly advertises worldwide", result.reason)

    def test_primary_description_can_supply_missing_global_location(self):
        for location in ("", "Remote"):
            with self.subTest(location=location):
                self.assert_status("qualified", location=location, description=DESCRIPTION + " This role is remote worldwide. You can work from anywhere.")

    def test_global_company_or_customer_base_is_not_global_hiring(self):
        for desc in (
            "We serve customers worldwide and have a globally distributed team.",
            "This is a fully remote company with global ambitions.",
            "You will own our global marketing strategy for a worldwide audience.",
            "We are a global remote team.",
            "Join our globally remote company.",
        ):
            with self.subTest(description=desc):
                self.assert_status("review", location="", description=DESCRIPTION + " " + desc)
                result = self.assert_status("qualified", location="Remote", description=DESCRIPTION + " " + desc)
                self.assertIn("country eligibility is unconfirmed", result.reason)

    def test_aggregator_global_claim_never_qualifies(self):
        for source in ("web3.career", "remoteok.com", "@web3marketingjobs", "x.com", "", "lever/"):
            with self.subTest(source=source):
                self.assert_status("review", source=source)

    def test_ats_url_does_not_authenticate_aggregator_description(self):
        self.assert_status("review", source="cryptojobslist.com", url="https://boards.greenhouse.io/example/jobs/123")

    def test_greenhouse_custom_career_urls_keep_primary_provenance(self):
        self.assert_status("qualified", source="greenhouse/example", url="https://example.com/careers/123")

    def test_country_region_city_locations_rejected(self):
        for location in (
            "Remote - US", "Remote (USA)", "Remote US/Canada", "Remote - U.S.",
            "Remote - India", "Remote - APAC", "Remote - EMEA", "Remote - Europe",
            "Remote - LATAM", "Worldwide, excluding India", "Remote (Estonia)",
            "Remote - Tallinn", "Singapore", "India", "Remote - EU only",
            "Worldwide - India only", "Remote (London)", "Worldwide except sanctioned countries",
        ):
            with self.subTest(location=location):
                self.assert_status("rejected", location=location)

    def test_office_and_hybrid_locations_rejected(self):
        for location in ("Hybrid", "Remote / On-site", "In-office", "Office-based"):
            with self.subTest(location=location):
                self.assert_status("rejected", location=location)

    def test_unrecognised_location_never_overridden_by_worldwide_prose(self):
        for location in ("Remote - Bucharest", "Remote - Ljubljana", "Remote (CA)", "Remote - ZZ", "Remote, Worldwide (Bucharest only)"):
            with self.subTest(location=location):
                result = classify_job(job(location=location, description=DESCRIPTION + " Work from anywhere in the world."), now=NOW)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_adversarial_residency_and_office_clauses_never_qualify(self):
        for clause in (
            "Candidates based in Bucharest are eligible.",
            "We can currently hire in the following countries: Canada and United States.",
            "Applicants must hold an EU passport.",
            "This role requires US residency.",
            "You must work from Ireland.",
            "You must be based within EMEA.",
            "You must be willing to relocate to Berlin.",
            "Eligible locations: Poland, Germany, UK.",
            "Eligible locations: Ljubljana.",
            "Candidates must reside within commuting distance of our Berlin office.",
            "This role is fully remote, but hiring is limited to countries where we have an employing entity.",
            "We are hiring candidates in Canada and the United States.",
            "Work in the New York office three days per week.",
            "We do not offer remote work for this role.",
            "This position isn't remote.",
            "This role is not fully remote.",
            "Remote work is unavailable.",
            "Applicants need to be a US resident.",
            "Work location must be within Australia.",
            "Applicants may not be based in India.",
        ):
            with self.subTest(clause=clause):
                result = classify_job(job(description=DESCRIPTION + " " + clause), now=NOW)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_negated_worldwide_claim_is_not_positive_evidence(self):
        for clause in ("We don't hire worldwide.", "We cannot work from anywhere.", "We do not support globally remote work."):
            with self.subTest(clause=clause):
                result = classify_job(job(location="Remote", description=DESCRIPTION + " " + clause), now=NOW)
                self.assertNotEqual("qualified", result.status, result.reason)

    def test_description_residency_restrictions_override_worldwide(self):
        for desc in (
            "You must be based in the United States.",
            "Applicants must reside in Canada.",
            "You need to be located in Singapore.",
            "You must be based in Tallinn.",
            "Candidates based in India are eligible.",
            "Only applicants living in Europe may apply.",
            "This remote role is limited to APAC candidates.",
            "We only hire in the US or Canada.",
            "This role is remote within the United Kingdom.",
            "This role is remote across Europe.",
            "This role is remote (US).",
            "Open to applicants from Canada.",
            "Available to candidates based in India.",
            "Remote worldwide, excluding India.",
            "We cannot hire in India.",
            "You must relocate to our headquarters.",
            "Residency required.",
        ):
            with self.subTest(description=desc):
                self.assert_status("rejected", description=DESCRIPTION + " " + desc)

    def test_work_authorization_requirements_rejected(self):
        for desc in (
            "Applicants must be authorized to work in the US.",
            "You need the right to work in the United Kingdom.",
            "Work authorization in Canada is required.",
            "You must be legally authorised to work in Australia.",
        ):
            with self.subTest(description=desc):
                self.assert_status("rejected", description=DESCRIPTION + " " + desc)

    def test_regular_office_attendance_rejected(self):
        for desc in (
            "You must work in our office three days every week.",
            "This is a hybrid role.",
            "We require 2 days per week in the office.",
            "You are required to attend our office regularly.",
        ):
            with self.subTest(description=desc):
                self.assert_status("rejected", description=DESCRIPTION + " " + desc)

    def test_timezones_are_flagged_separately(self):
        for desc in (
            "You must have 4 hours of overlap with US working hours.",
            "Must be based in a European timezone.",
            "Must be based in the European time zone.",
            "Must be based in an EU timezone.",
            "Work 14:00-18:00 UTC for synchronous meetings.",
            "The role requires overlap with CET business hours.",
        ):
            with self.subTest(description=desc):
                result = self.assert_status("qualified", description=DESCRIPTION + " " + desc)
                self.assertIn("availability check", result.timezone_note)

    def test_timezone_location_keeps_availability_separate(self):
        result = self.assert_status("qualified", location="Remote - UTC+2 to UTC+8")
        self.assertIn("country eligibility is unconfirmed", result.reason)
        self.assertTrue(result.timezone_note)
        result = self.assert_status("qualified", location="Remote - US timezone", description=DESCRIPTION + " Work from anywhere in the world.")
        self.assertTrue(result.timezone_note)

    def test_country_residency_with_timezone_still_rejected(self):
        self.assert_status("rejected", description=DESCRIPTION + " You must be based in the US with timezone overlap.")

    def test_geography_in_title_does_not_sneak_through(self):
        self.assert_status("rejected", title="Growth Lead (US Remote)")
        self.assert_status("review", title="Growth Lead - APAC")

    def test_company_hq_or_other_team_location_does_not_restrict_applicant(self):
        self.assert_status("qualified", description=DESCRIPTION + " Our company is headquartered in Singapore. Our design team is based in London. We have an office in India.")

    def test_negated_residency_or_office_requirement_not_rejected(self):
        self.assert_status("qualified", description=DESCRIPTION + " You are not required to be based in the US. No office attendance is required. Remote with no geographic restrictions.")

    def test_html_and_entities_are_normalised(self):
        self.assert_status("qualified", title="Senior Growth &amp; GTM Lead", description="<p>" + DESCRIPTION + "</p><ul><li>Work from anywhere.</li></ul>")
        self.assert_status("rejected", description="<p>" + DESCRIPTION + "</p><li>You must be based in <strong>India</strong>.</li>")

    def test_india_employer_is_rejected(self):
        for country in ("India", "IN", "IND", "Republic of India"):
            with self.subTest(country=country):
                self.assert_status("rejected", employer_country=country)

    def test_india_hq_in_description_is_rejected(self):
        for desc in ("We are headquartered in India.", "Our company is based in India.", "We are an India-based company."):
            with self.subTest(description=desc):
                self.assert_status("rejected", employer_country="", description=DESCRIPTION + " " + desc)

    def test_unknown_country_or_no_evidence_is_review(self):
        for changes in (
            {"employer_country": ""}, {"employer_country": "Worldwide"},
            {"employer_country_source": ""}, {"employer_country_source": "not a URL"},
            {"employer_country_verified_at": ""}, {"employer_country_verified_at": "today"},
            {"employer_country_verified_at": "2026-01-01"},
            {"employer_country_verified_at": "2026-12-01"},
        ):
            with self.subTest(changes=changes):
                self.assert_status("review", **changes)

    def test_india_office_not_same_as_india_employer(self):
        self.assert_status("qualified", employer_country="United States", description=DESCRIPTION + " Our Bengaluru office in India supports the engineering team.")

    def test_missing_optional_metadata_backward_compatible_but_review(self):
        listing = job()
        for field in ("employer_country", "employer_country_source", "employer_country_verified_at"):
            delattr(listing, field)
        self.assertEqual("review", classify_job(listing, now=NOW).status)

    def test_missing_unknown_or_future_dates_are_review(self):
        for posted in ("", None, "Unknown", "2027-01-01"):
            with self.subTest(posted=posted):
                self.assert_status("review", posted=posted)

    def test_stale_date_is_rejected(self):
        self.assert_status("rejected", posted="2026-01-01")
        self.assert_status("rejected", posted="31d")
        self.assert_status("qualified", posted="30d")

    def test_age_window_includes_new_posts_through_thirty_days(self):
        for delta, expected in (
            (timedelta(), "qualified"),
            (timedelta(microseconds=1), "qualified"),
            (timedelta(days=1), "qualified"),
            (timedelta(days=3), "qualified"),
            (timedelta(days=13), "qualified"),
            (timedelta(days=14) - timedelta(microseconds=1), "qualified"),
            (timedelta(days=14), "qualified"),
            (timedelta(days=21), "qualified"),
            (timedelta(days=30), "qualified"),
            (timedelta(days=30) + timedelta(microseconds=1), "rejected"),
            (timedelta(days=31), "rejected"),
        ):
            with self.subTest(delta=delta):
                self.assert_status(expected, posted=(NOW - delta).isoformat())
        self.assert_status("review", posted=(NOW + timedelta(microseconds=1)).isoformat())

    def test_recent_relative_dates_can_qualify_immediately(self):
        for posted in ("now", "today", "just now", "yesterday", "1h", "1d", "3d", "13d", "14d", "30d"):
            with self.subTest(posted=posted):
                self.assert_status("qualified", posted=posted)

    def test_recent_date_does_not_bypass_other_evidence_checks(self):
        for changes, expected in (
            ({"title": "Community Lead"}, "rejected"),
            ({"location": "Remote - United States"}, "rejected"),
            ({"employer_country": "India"}, "rejected"),
            ({"location": ""}, "review"),
            ({"employer_country": ""}, "review"),
            ({"source": "aggregator"}, "review"),
            ({"description": ""}, "review"),
        ):
            with self.subTest(changes=changes):
                self.assert_status(expected, posted=NOW.isoformat(), **changes)

    def test_no_application_url_is_review(self):
        self.assert_status("review", url="")

    def test_decision_preserves_original_job_and_includes_reason(self):
        listing = job()
        original = dict(vars(listing))
        decision = classify_job(listing, now=NOW)
        self.assertIs(listing, decision.job)
        self.assertEqual(original, vars(listing))
        self.assertIsInstance(decision, FilterDecision)
        self.assertIn("First-party", decision.reason)

    def test_partition_and_legacy_wrapper(self):
        good = job(id="good")
        review = job(id="review", location="")
        bad = job(id="bad", title="Community Lead")
        result = filter_jobs([good, review, bad], now=NOW)
        self.assertEqual([good], result.qualified)
        self.assertEqual([review], [item.job for item in result.review])
        self.assertEqual([bad], [item.job for item in result.rejected])
        with patch("filters.filter_jobs", return_value=result):
            self.assertEqual([good], apply_filters([good, review, bad]))

    def test_empty_input(self):
        result = filter_jobs([], now=NOW)
        self.assertEqual([], result.qualified)
        self.assertEqual([], result.review)
        self.assertEqual([], result.rejected)


class DateParsingTests(unittest.TestCase):
    def test_iso_and_rfc_formats(self):
        expected = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        for posted in ("2026-10-18T12:00:00Z", "2026-10-18T14:00:00+02:00", "2026-10-18 12:00:00", "Sun, 18 Oct 2026 12:00:00 GMT"):
            with self.subTest(posted=posted):
                self.assertEqual(expected, _parse_posted_date(posted))

    def test_unix_seconds_and_milliseconds(self):
        for posted in (str(int(NOW.timestamp())), str(int(NOW.timestamp() * 1000))):
            self.assertEqual(NOW, _parse_posted_date(posted))

    def test_relative_formats(self):
        for posted, delta in (
            ("16h", timedelta(hours=16)), ("2d", timedelta(days=2)),
            ("3w", timedelta(weeks=3)), ("1mo", timedelta(days=30)),
            ("1m", timedelta(days=30)), ("2 days ago", timedelta(days=2)),
            ("2 hours ago", timedelta(hours=2)), ("2 minutes ago", timedelta(minutes=2)),
            ("today", timedelta()), ("just now", timedelta()), ("yesterday", timedelta(days=1)),
        ):
            with self.subTest(posted=posted):
                self.assertEqual(NOW - delta, _parse_posted_date(posted, now=NOW))

    def test_invalid_formats_remain_unknown(self):
        for posted in (None, "", "None", "0", "yesterday-ish", "invalid", "999999999999999999999999999999999d"):
            self.assertIsNone(_parse_posted_date(posted, now=NOW))


if __name__ == "__main__":
    unittest.main()

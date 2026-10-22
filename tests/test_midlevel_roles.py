"""Experienced growth/GTM/PMM contributors do not need leadership titles."""

import unittest
from datetime import timedelta

from filters import classify_job
from test_filters import NOW, job


OWNERSHIP = (
    "Own product positioning, messaging, product launches and activation. "
    "Build growth experiments across acquisition and retention, working with "
    "product and analytics to measure results and improve conversion. "
)


class MidlevelRoleTests(unittest.TestCase):
    def decision(self, **changes):
        fields = dict(
            title="Growth Specialist", location="Remote",
            description=OWNERSHIP + "Requirements: 3–5 years of growth marketing experience.",
        )
        fields.update(changes)
        return classify_job(job(**fields), now=NOW)

    def test_product_marketing_manager_with_three_years_qualifies(self):
        result = self.decision(
            title="Product Marketing Manager",
            description=OWNERSHIP + "You have 3+ years of product marketing experience.",
        )
        self.assertEqual("qualified", result.status, result.reason)

    def test_growth_specialist_with_three_to_five_years_qualifies(self):
        result = self.decision()
        self.assertEqual("qualified", result.status, result.reason)
        self.assertIn("country eligibility is unconfirmed", result.reason)

    def test_experienced_growth_marketer_needs_no_leadership_title(self):
        result = self.decision(
            title="Growth Marketer",
            description=OWNERSHIP + "You bring five years of relevant marketing experience.",
        )
        self.assertEqual("qualified", result.status, result.reason)

    def test_experienced_core_specialists_and_managers_qualify(self):
        for title in (
            "GTM Specialist", "Go-to-Market Manager", "Product Marketing Specialist",
            "Growth Marketing Specialist", "Growth Marketing Manager", "PMM",
            "Marketing Specialist",
        ):
            with self.subTest(title=title):
                result = self.decision(title=title)
                self.assertEqual("qualified", result.status, result.reason)

    def test_three_year_experience_spellings_qualify(self):
        for requirement in (
            "3 years of experience in marketing.",
            "3+ years of relevant experience.",
            "3 to 5 years of growth marketing experience.",
            "three years of professional experience.",
            "3 years of GTM experience.",
            "3+ years in product marketing, including B2B.",
        ):
            with self.subTest(requirement=requirement):
                result = self.decision(description=OWNERSHIP + requirement)
                self.assertEqual("qualified", result.status, result.reason)

    def test_short_crypto_tenure_does_not_override_midlevel_overall_experience(self):
        result = self.decision(description=OWNERSHIP + (
            "We require 3+ years of product marketing experience and "
            "1 year of experience in crypto."
        ))
        self.assertEqual("qualified", result.status, result.reason)

    def test_junior_intern_and_support_titles_still_rejected(self):
        for title in (
            "Junior Growth Specialist", "Growth Intern", "Product Marketing Intern",
            "Growth Marketing Assistant", "GTM Associate", "Marketing Coordinator",
        ):
            with self.subTest(title=title):
                result = self.decision(title=title)
                self.assertEqual("rejected", result.status, result.reason)

    def test_early_career_descriptions_still_rejected(self):
        for requirement in (
            "No previous experience required.", "No experience is required.",
            "This is an entry-level role.", "0-2 years of relevant experience.",
            "1 to 3 years of marketing experience.", "2 years of growth marketing experience.",
            "0-5 years of product marketing experience.", "1 to 5 years of GTM experience.",
            "2+ years of product marketing experience.",
            "one to three years of product marketing experience.",
            "zero to five years of GTM experience.",
            "0 to five years of relevant experience.",
            "2-5 years of experience in crypto.",
            "one to five years in product marketing.",
            "2-5 years in growth marketing.",
        ):
            with self.subTest(requirement=requirement):
                result = self.decision(description=OWNERSHIP + requirement)
                self.assertEqual("rejected", result.status, result.reason)

    def test_specialist_without_experience_evidence_stays_review(self):
        result = self.decision(description=OWNERSHIP)
        self.assertEqual("review", result.status, result.reason)
        self.assertIn("experience needs verification", result.reason)

    def test_vague_support_scope_stays_review(self):
        result = self.decision(description=(
            "Support our marketing team with assigned campaigns and administrative tasks. "
            "Help colleagues prepare materials and learn about growth marketing as you go."
        ))
        self.assertEqual("review", result.status, result.reason)

    def test_experience_alone_does_not_supply_relevant_ownership(self):
        result = self.decision(description=(
            "You bring 4 years of marketing experience. Help colleagues prepare assets, "
            "assist with campaigns and report updates to the team every week."
        ))
        self.assertEqual("review", result.status, result.reason)
        self.assertIn("does not establish ownership", result.reason)

    def test_experienced_web3_content_marketing_is_relevant(self):
        result=self.decision(title='Senior Content Writer (Web3)',description=(
            'Own the editorial strategy and brand storytelling for our Web3 products. '
            'Develop educational campaigns, interview customers and create social content '
            'for a crypto-native audience. You have 4 years of marketing experience.'
        ))
        self.assertEqual('qualified',result.status,result.reason)

    def test_technical_documentation_is_not_marketing_content(self):
        result=self.decision(title='Senior Technical Writer',description=(
            'Own the technical API documentation for blockchain developers. '
            'Write reference manuals and integration specifications with engineering. '
            'You have 4 years of professional experience.'
        ))
        self.assertNotEqual('qualified',result.status)

    def test_content_only_scope_does_not_qualify(self):
        result = self.decision(description=(
            "Own the editorial calendar and develop blog articles and social posts. "
            "You have 4 years of marketing experience producing content and copy."
        ))
        self.assertEqual("review", result.status, result.reason)

    def test_adjacent_content_specialties_do_not_qualify_from_growth_keywords(self):
        for title in ("Content Marketing Specialist", "Brand Marketing Manager"):
            with self.subTest(title=title):
                result = self.decision(title=title)
                self.assertEqual("review", result.status, result.reason)

    def test_bd_community_and_event_titles_still_rejected(self):
        for title in (
            "Business Development Manager", "Community Growth Specialist", "Social Media Marketing Manager",
            "Event Marketing Manager", "Growth and Partnerships Manager",
        ):
            with self.subTest(title=title):
                result = self.decision(title=title)
                self.assertEqual("rejected", result.status, result.reason)

    def test_disguised_bd_community_or_events_still_rejected(self):
        for clause in (
            "You will carry a sales quota and close deals.",
            "Own community management across Discord and Telegram.",
            "Your primary focus is events and conferences.",
        ):
            with self.subTest(clause=clause):
                result = self.decision(description=OWNERSHIP + "3+ years of experience. " + clause)
                self.assertEqual("rejected", result.status, result.reason)

    def test_other_hard_filters_are_unchanged_for_midlevel_roles(self):
        for changes in (
            {"location": "Remote - US"}, {"location": "Hybrid"},
            {"employer_country": "India"},
            {"posted": (NOW - timedelta(days=31)).isoformat()},
            {"description": OWNERSHIP + "3+ years of experience. Applicants must live in Europe."},
        ):
            with self.subTest(changes=changes):
                result = self.decision(**changes)
                self.assertEqual("rejected", result.status, result.reason)

    def test_missing_source_base_or_recency_evidence_still_needs_review(self):
        for changes in (
            {"source": "web3.career"}, {"employer_country": ""}, {"posted": ""},
            {"location": ""}, {"description": "Own growth strategy. 3 years experience."},
        ):
            with self.subTest(changes=changes):
                result = self.decision(**changes)
                self.assertEqual("review", result.status, result.reason)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from filters import _is_primary_source

class DirectSourceIdentityTests(unittest.TestCase):
    def test_verified_jito_board_is_enabled(self):
        from boards import LEVER_COMPANIES
        self.assertIn(("jito", "Jito Foundation"), LEVER_COMPANIES)

    def test_official_company_pages_are_primary_only_with_matching_source_and_host(self):
        for source, url in [
            ("company/gains.trade", "https://hiring.gains.trade/roles/head-of-growth/"),
            ("company/hacken.io", "https://hacken.io/careers/175289-growth-marketing-manager/"),
        ]:
            self.assertTrue(_is_primary_source(SimpleNamespace(source=source, url=url)))
            self.assertFalse(_is_primary_source(SimpleNamespace(source="x.com", url=url)))
            self.assertFalse(_is_primary_source(SimpleNamespace(source="web3.career", url=url)))
            self.assertFalse(_is_primary_source(SimpleNamespace(source=source, url=url.replace("https:", "http:"))))
            self.assertFalse(_is_primary_source(SimpleNamespace(source=source, url=url.replace(".io/", ".io.evil.test/").replace(".trade/", ".trade.evil.test/"))))

    def test_company_label_does_not_trust_unrelated_page_or_unknown_company(self):
        for source, url in [
            ("company/gains.trade", "https://hiring.gains.trade/not-a-role/"),
            ("company/gains.trade", "https://hacken.io/careers/1/"),
            ("company/unknown", "https://example.test/careers/1/"),
            ("company/hacken.io", "https://hacken.io/about/"),
        ]:
            self.assertFalse(_is_primary_source(SimpleNamespace(source=source, url=url)))

if __name__ == "__main__":
    unittest.main()

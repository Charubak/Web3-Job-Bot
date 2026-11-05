"""Source expiry is an instant, except inclusive date-only deadlines."""
import unittest
from datetime import datetime, timezone

import company_roles as curated
from test_company_roles import role_page, schema, TYB_INDEX, tyb_body


NOW = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)


class SourceExpiryTests(unittest.TestCase):
    def test_expiry_boundaries_and_offsets(self):
        cases = [('2026-11-05T07:00:00Z', True),
                 ('2026-11-05T08:00:00Z', True),
                 ('2026-11-04T23:00:00-12:00', False),
                 ('2026-11-05T23:00:00+15:00', True),
                 ('2026-11-05', False), ('2026-11-04', True),
                 ('', False), ('invalid', False)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, curated._expiry_passed(value, NOW))

    def test_curated_role_respects_exact_expiry_instant(self):
        source = curated.ROLE_SOURCES[0]
        for expiry, count in [('2026-11-05T07:00:00Z', 0),
                              ('2026-11-04T23:00:00-12:00', 1), ('2026-11-05', 1)]:
            with self.subTest(expiry=expiry):
                page = role_page(source) + schema(source, validThrough=expiry)
                self.assertEqual(count, len(curated._parse_role(page, source, now=NOW)))

    def test_tyb_both_sources_respect_expiry_instant(self):
        index_source = curated.RoleSource(curated.TYB_SOGAL_URL, 'TYB', curated.TYB_TITLE, (), False)
        for expiry, count in [('2026-11-05T07:00:00Z', 0),
                              ('2026-11-04T23:00:00-12:00', 1), ('2026-11-05', 1)]:
            for index, body in [(TYB_INDEX, tyb_body(validThrough=expiry)),
                                (TYB_INDEX + schema(index_source, validThrough=expiry), tyb_body())]:
                with self.subTest(expiry=expiry, index_has_expiry='validThrough' in index):
                    self.assertEqual(count, len(curated._parse_tyb(index, body, now=NOW)))


if __name__ == '__main__':
    unittest.main()

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from types import SimpleNamespace
from boards import Job
from reviewed_roles import reviewed_details, fetch_reviewed_roles, ZONIQX, BINANCE

NOW=datetime(2026,10,23,tzinfo=timezone.utc)
class ReviewedRolesTests(unittest.TestCase):
    def zoniqx(self):
        return Job('z','Marketing Manager','Zoniqx','India (Remote)',ZONIQX,'reviewed/zoniqx',
                   posted='2026-10-22', description='India (Remote). Own and run marketing across channels. 2+ years.',current_open_verified=True)
    def test_exact_reviewed_role_included_but_changed_closed_or_old_withheld(self):
        j=self.zoniqx(); self.assertIsNotNone(reviewed_details(j,NOW))
        j.title='Social Media Manager'; self.assertIsNone(reviewed_details(j,NOW))
        j=self.zoniqx();j.current_open_verified=False;self.assertIsNone(reviewed_details(j,NOW))
        j=self.zoniqx();j.published_at_upper_bound='2026-01-01';self.assertIsNone(reviewed_details(j,NOW))
        self.assertIsNone(reviewed_details(self.zoniqx(),datetime(2026,11,22,tzinfo=timezone.utc)))
    def test_binance_is_explicitly_adjacent_with_unresolved_entity_note(self):
        j=Job('b','Product Marketing Manager - South Asia','Binance','India',BINANCE,'greenhouse/binance',
              posted='2026-10-13',description='5+ years including 3 years Web3. Own go-to-market. Remote-first.',current_open_verified=True)
        details=reviewed_details(j,NOW)
        self.assertTrue(details['adjacent']); self.assertIn('employing entity',details['note'])
        j.location='US only';self.assertIsNone(reviewed_details(j,NOW))
    def test_live_social_page_requires_identity_application_and_not_closed(self):
        html='<h1>Marketing Manager</h1><a class="topcard__org-name-link">Zoniqx Inc</a><button class="apply-button">Apply</button><div class="show-more-less-html__markup">India (Remote). Own and run marketing across channels. 2+ years.</div>'
        response=SimpleNamespace(text=html,raise_for_status=lambda:None)
        with patch('boards._get',return_value=response):
            self.assertEqual(1,len(fetch_reviewed_roles()))
            response.text=html+'No longer accepting applications'
            self.assertEqual([],fetch_reviewed_roles())
            response.text='<h1>Sign in</h1>'
            self.assertEqual([],fetch_reviewed_roles())

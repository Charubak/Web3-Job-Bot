import unittest
from unittest.mock import patch
from types import SimpleNamespace
import boards
from employers import enrich_employers
from filters import classify_job

class BinanceSourceTests(unittest.TestCase):
    def test_binance_greenhouse_board_is_enabled_without_removing_lever(self):
        self.assertIn(('binance','Binance'), boards.GREENHOUSE_COMPANIES)
        self.assertIn(('binance','Binance'), boards.LEVER_COMPANIES)

    def test_eu_listing_is_loaded_through_shared_api_without_inventing_remote_or_employer(self):
        payload={'jobs':[{'id':5002385101,'title':'Product Marketing Manager - South Asia',
          'absolute_url':'https://job-boards.eu.greenhouse.io/binance/jobs/5002385101',
          'location':{'name':'India'},'first_published':'2026-10-13T21:11:37-04:00',
          'content':'Own end-to-end go-to-market strategy, product positioning and lifecycle marketing. We require 5+ years of marketing experience including 3+ years in crypto/Web3. Remote-first, work-from-home arrangement may vary depending on the nature of the role and team.'}]}
        response=SimpleNamespace(status_code=200,json=lambda:payload)
        with patch('boards._get',return_value=response) as get:
            jobs=boards._fetch_greenhouse_one('binance','Binance')
        self.assertEqual('https://boards-api.greenhouse.io/v1/boards/binance/jobs',get.call_args.args[0])
        self.assertEqual(1,len(jobs));self.assertEqual('India',jobs[0].location)
        self.assertTrue(jobs[0].current_open_verified)
        self.assertEqual('2026-10-13T21:11:37-04:00',jobs[0].posted)
        decision=classify_job(enrich_employers(jobs)[0])
        self.assertNotEqual('qualified',decision.status)
        self.assertEqual('', jobs[0].employer_country)
        self.assertEqual('India', jobs[0].location)

if __name__=='__main__': unittest.main()

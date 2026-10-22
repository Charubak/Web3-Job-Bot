import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from boards import Job
import storage
import scraper
import daily_digest
from digest_content import messages


def job(n='1', posted='1d', active=True):
    return Job(n, 'Senior Growth Marketing Manager', 'Example', 'Remote - Worldwide',
               'https://jobs.lever.co/example/'+n, 'lever/example', posted=posted,
               current_open_verified=active)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'seen_jobs.db'
        for target in ('storage.DB_PATH','scraper.DB_PATH'):
            p=patch(target,self.db); p.start(); self.addCleanup(p.stop)
        storage.initialize_state()

    def report(self, jobs):
        results=SimpleNamespace(qualified=jobs,review=[],rejected=[])
        with patch('scraper.fetch_all',return_value=jobs), patch('scraper.filter_jobs',return_value=results):
            return scraper.main(digest=True,allow_empty=True)

    def test_shared_jobs_remain_in_digest_and_uncertain_jobs_are_withheld(self):
        old,new,pending=job('old'),job('new'),job('pending')
        storage.mark_seen([old]); storage.mark_unconfirmed([pending])
        before=self.db.read_bytes()
        r=self.report([old,new,pending])
        self.assertEqual({'old':'previously_shared','new':'new'}, {e['job']['id']:e['label'] for e in r['digest_jobs']})
        self.assertEqual(1,r['counts']['unconfirmed_delivery'])
        self.assertEqual(before,self.db.read_bytes())

    def test_reminder_preview_refuses_missing_history(self):
        self.db.unlink()
        with self.assertRaisesRegex(scraper.SetupError, 'Existing delivery history'):
            scraper.main(digest=True)

    def test_missing_live_open_confirmation_does_not_become_reminder(self):
        j=job(active=False); storage.mark_seen([j])
        r=self.report([j])
        self.assertEqual([],r['digest_jobs'])
        self.assertEqual(1,r['counts']['active_unverified'])

    def test_closed_job_absent_from_live_results_is_not_recycled_from_history(self):
        storage.mark_seen([job('closed')])
        self.assertEqual([],self.report([])['digest_jobs'])

    def test_unknown_age_is_separate_and_shared_role_not_called_new(self):
        known,unknown=job('known'),job('unknown',posted='')
        r={'digest_jobs':[{'job':asdict(known),'label':'new'}, {'job':asdict(unknown),'label':'previously_shared'}]}
        rendered='\n'.join(c['text'] for c in messages(r,'Summary'))
        self.assertIn('New — posted within 30 days',rendered)
        self.assertIn('not verified within 30 days',rendered)
        self.assertIn('Previously shared reminder',rendered)
        self.assertIn('original posting date unknown',rendered)

    def test_all_roles_are_chunked_without_old_ten_job_limit(self):
        entries=[{'job':asdict(job(str(n))),'label':'new'} for n in range(65)]
        chunks=messages({'digest_jobs':entries},'Summary')
        self.assertEqual(65,sum(len(c['new_jobs']) for c in chunks))
        self.assertTrue(all(len(c['text'])<=3800 for c in chunks))

    def test_review_card_acknowledgment_and_shared_page_title_are_required(self):
        a,b=job('a'),job('b'); b.url=a.url; b.title='Content Manager'
        conn=storage._connect()
        conn.execute('CREATE TABLE telegram_latest_review_jobs (id TEXT,url TEXT,title TEXT)')
        conn.execute('CREATE TABLE telegram_latest_job_review (status TEXT)')
        conn.execute('INSERT INTO telegram_latest_review_jobs VALUES (?,?,?)',('card',a.url,a.title))
        conn.execute("INSERT INTO telegram_latest_job_review VALUES ('pending')");conn.commit()
        self.assertEqual('unconfirmed',storage.delivery_labels([a])[a.id])
        conn.execute("UPDATE telegram_latest_job_review SET status='confirmed'");conn.commit();conn.close()
        self.assertEqual({'a':'previously_shared','b':'new'},storage.delivery_labels([a,b]))

    def test_real_filters_exclude_over_30_day_shared_role(self):
        from test_filters import DESCRIPTION
        j=job(posted='31d');j.description=DESCRIPTION
        j.employer_country='United Kingdom';j.employer_country_source='https://example.org/about'
        j.employer_country_verified_at=datetime.now(timezone.utc).date().isoformat()
        storage.mark_seen([j])
        with patch('scraper.fetch_all',return_value=[j]),patch('scraper.enrich_employers',side_effect=lambda x:x):
            r=scraper.main(digest=True,allow_empty=True)
        self.assertEqual([],r['digest_jobs'])
        self.assertEqual(1,r['counts']['rejected'])

if __name__=='__main__': unittest.main()

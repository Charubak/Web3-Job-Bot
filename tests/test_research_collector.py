"""Research regressions: no network, delivery or real state."""
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import boards
import filters
import source_health
from test_filters import DESCRIPTION, NOW, job


class GeographyRegressionTests(unittest.TestCase):
    def test_benefits_and_other_state_exclusions_do_not_prove_india_ineligibility(self):
        description = DESCRIPTION + '\nPerks:\n401k retirement plan + company match (US only)\nHome office equipment program\nThis role cannot be performed in California, or Colorado.'
        result = filters.classify_job(job(location='Remote', description=description), now=NOW)
        self.assertNotEqual('rejected', result.status, result.reason)
        self.assertEqual('review', result.status)
        self.assertIn('exclusion', result.reason.lower())

    def test_benefits_do_not_hide_a_real_us_hiring_restriction(self):
        for clause in ('Applicants must reside in the US.', 'We only hire in the US.', 'This role cannot be performed in India.'):
            with self.subTest(clause=clause):
                result = filters.classify_job(job(location='Remote', description=DESCRIPTION+'\n401k (US only)\n'+clause), now=NOW)
                self.assertEqual('rejected', result.status, result.reason)

    def test_ats_text_preserves_bullet_boundaries(self):
        text = boards._plain_text('<ul><li>401k (US only)</li><li>Home office equipment</li></ul><p>This role cannot be performed in California.</p>')
        self.assertIn('\n', text)
        normalized = filters._normalise(text)
        self.assertRegex(normalized, r'us only\).*?\.\s+home office')

    def test_false_positive_repair_preserves_negative_controls(self):
        for changes in ({'location':'Remote - US'}, {'location':'Hybrid'}, {'employer_country':'India'}, {'title':'Community Manager'}, {'title':'Business Development Lead'}, {'title':'Junior Marketing Manager'}, {'title':'Social Media Manager'}, {'title':'Event Marketing Manager'}):
            with self.subTest(changes=changes):
                self.assertEqual('rejected',filters.classify_job(job(**changes),now=NOW).status)


class RequestHealthTests(unittest.TestCase):
    def setUp(self):
        source_health.reset()

    def test_request_evidence_has_no_query_values_or_userinfo(self):
        def fetch_example():
            source_health.record_request('https://name:secret@example.org/jobs?token=private&q=public', 403, 0.2)
            source_health.record_failure()
            return []
        source_health.run_source(fetch_example)
        row=source_health.snapshot()[0]
        self.assertEqual('unavailable',row['status'])
        self.assertEqual(403,row['requests'][0]['status_code'])
        self.assertEqual('https://example.org/jobs',row['requests'][0]['url'])
        self.assertNotIn('secret',json.dumps(row))
        self.assertNotIn('private',json.dumps(row))

    def test_zero_results_are_explicit_and_not_claimed_complete(self):
        def fetch_twitter_bing(): return []
        source_health.run_source(fetch_twitter_bing)
        self.assertEqual('zero_results',source_health.snapshot()[0]['warning'])


class PublicCollectorTests(unittest.TestCase):
    def sample(self, **changes):
        fields = dict(id='sample', title='Senior Growth Marketing Manager', company='Example',
            location='Remote', url='https://jobs.lever.co/example/123', source='lever/example',
            posted=NOW.isoformat(), description=DESCRIPTION, current_open_verified=True,
            employer_country='US', employer_country_source='https://example.org/about',
            employer_country_verified_at='2026-10-18')
        fields.update(changes)
        return boards.Job(**fields)

    def build(self, jobs, previous=None):
        import research_collector as c
        with patch('research_collector.enrich_employers',side_effect=lambda rows:rows):
            return c.build_report(jobs, previous or {}, [], now=NOW)

    def test_recent_unconfirmed_open_is_researchable_with_explicit_label(self):
        report,_=self.build([self.sample(current_open_verified=False)])
        row=report['candidates'][0]
        self.assertEqual('qualified',row['classification'])
        self.assertEqual('unconfirmed_open',row['opening_status'])
        self.assertEqual('within_30_days',row['age_status'])
        self.assertTrue(row['research_tasks'])
        self.assertEqual(1,report['counts']['unconfirmed_open'])

    def test_collection_history_never_labels_delivery_or_fabricates_age(self):
        import research_collector as c
        first,hist=self.build([self.sample(posted='')])
        second,_=self.build([self.sample(posted='')],hist['jobs'])
        self.assertEqual('first_collection',first['candidates'][0]['collection_status'])
        self.assertEqual('previously_collected',second['candidates'][0]['collection_status'])
        self.assertEqual('unknown',second['candidates'][0]['age_status'])
        self.assertNotIn('previously_shared',json.dumps(second))
        self.assertNotIn('recipient',json.dumps(hist))

    def test_same_url_cannot_become_fresh_after_expiry(self):
        from datetime import timedelta
        import research_collector as c
        item=self.sample()
        old=(NOW-timedelta(days=40)).isoformat()
        prev={c.identity(item):{'url':item.url,'first_seen_at':old,'first_posted_at':old,'expired_at':old}}
        report,hist=self.build([item],prev)
        self.assertEqual([],report['candidates'])
        self.assertEqual('older_than_30_days',report['rejected_candidates'][0]['age_status'])
        self.assertEqual(old,hist['jobs'][c.identity(item)]['expired_at'])

    def test_same_title_different_requisitions_do_not_share_dates(self):
        older=self.sample(id='old',url='https://jobs.lever.co/example/old',posted='2026-09-01')
        newer=self.sample(id='new',url='https://jobs.lever.co/example/new')
        report,_=self.build([older,newer])
        self.assertEqual(1,len(report['candidates']))
        self.assertEqual(newer.posted,report['candidates'][0]['posted'])
        self.assertEqual(older.url,report['candidates'][0]['related_evidence'][0]['url'])

    def test_export_has_no_arbitrary_job_or_recipient_fields(self):
        item=self.sample(description=DESCRIPTION+' contact john@example.org https://example.org/apply?token=private')
        item.recipient='secret-recipient'; item.telegram_token='secret-token'
        report,hist=self.build([item])
        serialized=json.dumps([report,hist])
        for forbidden in ('john@example.org','token=private','secret-recipient','secret-token'):
            self.assertNotIn(forbidden,serialized)

    def test_sqlite_seed_is_readonly_and_only_history_is_exported(self):
        import sqlite3
        import research_collector as c
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE job_history(url,first_seen_at,first_posted_at,expired_at)')
                conn.execute('INSERT INTO job_history VALUES(?,?,?,?)',('https://example.org/job','2026-10-01',None,None))
                conn.execute('CREATE TABLE recipient(token,chat_id)')
                conn.execute("INSERT INTO recipient VALUES('secret-token','secret-recipient')")
            before=path.read_bytes();history=c.seed_history(path)
            self.assertEqual(before,path.read_bytes())
            self.assertEqual(1,len(history))
            self.assertNotIn('secret',json.dumps(history))

    def test_closed_absent_roles_are_not_recycled_from_history(self):
        _,history=self.build([self.sample()])
        report,_=self.build([],history['jobs'])
        self.assertEqual([],report['candidates'])
        self.assertEqual(0,report['counts']['unconfirmed_open'])

    def test_collection_writes_only_allowlisted_files_and_never_delivery_state(self):
        import research_collector as c
        with tempfile.TemporaryDirectory() as directory:
            with patch('research_collector.fetch_all',return_value=[self.sample()]), patch('research_collector.enrich_employers',side_effect=lambda rows:rows), patch('notifier._send',side_effect=AssertionError('must never send')):
                c.collect(directory)
            self.assertEqual({'latest.json','latest.md','history.json','candidates','rejections','research-queue'}, {p.name for p in Path(directory).iterdir()})
            manifest=json.loads((Path(directory)/'latest.json').read_text())
            self.assertEqual(['candidates/part-001.json'],manifest['candidate_files'])
            self.assertNotIn('candidates',manifest)

    def test_connector_shards_are_bounded_without_dropping_rows(self):
        from research_collector import write_shards
        with tempfile.TemporaryDirectory() as directory:
            rows=[{'title':'Marketing '+str(i),'excerpt':'x'*1500} for i in range(30)]
            paths=write_shards(Path(directory),'candidates',rows,limit=4000)
            restored=[]
            for path in paths:
                file=Path(directory)/path
                self.assertLessEqual(file.stat().st_size,4000)
                restored.extend(json.loads(file.read_text()))
            self.assertEqual(rows,restored)

    def test_public_urls_reject_local_and_credential_urls(self):
        from research_collector import public_url
        for url in ('http://localhost/job','http://127.0.0.1/job','http://169.254.169.254/', 'https://u:secret@example.org/job','mailto:a@example.org'):
            self.assertEqual('',public_url(url))
        self.assertEqual('https://example.org/job?id=123',public_url('https://example.org/job?id=123&token=secret&utm_source=x'))


class WorkflowIsolationTests(unittest.TestCase):
    def test_research_workflow_never_receives_delivery_secrets_or_runs_send(self):
        root=Path(__file__).resolve().parents[1]
        workflow=(root/'.github/workflows/research-collection.yml').read_text()
        for value in ('secrets.', 'daily_digest.py', 'scraper.py --send', '--prepare-send', '--deliver-prepared'):
            self.assertNotIn(value,workflow)
        self.assertIn('contents: read',workflow.split('jobs:',1)[0])
        self.assertIn('research_collector.py --output public-export',workflow)
        self.assertIn('public-export/candidates/*.json',workflow)
        self.assertNotIn('path: data/',workflow.split('Archive allowlisted',1)[1])

    def test_cutover_disables_legacy_delivery_but_not_collection(self):
        root=Path(__file__).resolve().parents[1]
        legacy=(root/'.github/workflows/jobs.yml').read_text()
        research=(root/'.github/workflows/research-collection.yml').read_text()
        self.assertIn("vars.JOB_DIGEST_DELIVERY != 'chatgpt'",legacy)
        self.assertNotIn('JOB_DIGEST_DELIVERY',research)

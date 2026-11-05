"""Offline safeguards for the legacy-to-research wake-up bridge."""
from pathlib import Path
import unittest


class CollectionWakeupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.workflow = (root / '.github/workflows/research-collection.yml').read_text()
        cls.triggers = cls.workflow.split('permissions:', 1)[0]
        cls.guard = cls.workflow.split('    if: >-', 1)[1].split('    runs-on:', 1)[0]
        cls.legacy = (root / '.github/workflows/jobs.yml').read_text()

    def test_existing_mac_dispatch_reaches_collector_even_when_delivery_skipped(self):
        self.assertIn("workflows: ['Scheduled job search']", self.triggers)
        self.assertIn('types: [completed]', self.triggers)
        self.assertIn("github.event.workflow_run.event == 'workflow_dispatch'", self.guard)
        # success-only gating would reproduce the cutover bug.
        self.assertNotIn('conclusion', self.guard)
        self.assertIn("vars.JOB_DIGEST_DELIVERY != 'chatgpt'", self.legacy)

    def test_upstream_runs_must_be_from_this_repository_and_main(self):
        self.assertIn("github.repository == 'Charubak/Web3-Job-Bot'", self.guard)
        self.assertIn('github.event.repository.private == false', self.guard)
        self.assertIn("github.ref == 'refs/heads/main'", self.guard)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", self.guard)
        self.assertIn('github.event.workflow_run.head_repository.full_name == github.repository', self.guard)

    def test_only_main_collector_changes_refresh_on_push(self):
        push = self.triggers.split('  push:', 1)[1].split('  workflow_dispatch:', 1)[0]
        self.assertIn('branches: [main]', push)
        self.assertIn("'.github/workflows/research-collection.yml'", push)
        self.assertNotIn('research-data', push)
        self.assertNotIn('pull_request:', self.triggers)

    def test_classification_and_transitive_source_inputs_refresh_evidence(self):
        push = self.triggers.split('  push:', 1)[1].split('  workflow_dispatch:', 1)[0]
        for path in ('boards.py', 'research_collector.py', 'filters.py', 'employers.py',
                     'employers.json', 'company_roles.py', 'direct_sources.py',
                     'reviewed_roles.py', 'career_discovery.py', 'hylo_source.py',
                     'linkedin_discovery.py', 'source_health.py', 'requirements.txt'):
            self.assertIn(f"'{path}'", push)

    def test_no_feedback_loop_or_parallel_publication(self):
        self.assertNotIn("workflows: ['Public job research collection']", self.triggers)
        self.assertIn('group: public-job-research-collection', self.workflow)
        self.assertIn('cancel-in-progress: false', self.workflow)
        self.assertIn('git push origin HEAD:research-data', self.workflow)
        self.assertNotIn('gh workflow run', self.workflow)

    def test_bridge_has_no_delivery_or_credentials(self):
        for forbidden in ('secrets.', 'daily_digest.py', 'mode=send', 'TELEGRAM_BOT_TOKEN'):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn('contents: read', self.workflow.split('jobs:', 1)[0])
        self.assertIn('python research_collector.py --output public-export', self.workflow)


if __name__ == '__main__':
    unittest.main()

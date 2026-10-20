import unittest
from types import SimpleNamespace
from notifier import _format_job
from filters import classify_job
from test_filters import DESCRIPTION, NOW, job


class ConciseRecommendationTests(unittest.TestCase):
    def test_short_message_omits_estimates_and_evidence_essay(self):
        j=SimpleNamespace(title='Senior Product Marketing Manager', company='Example',
                          url='https://example.test/apply', salary='$999k estimated',
                          posted='1d', location='Remote', description='US timezone overlap', source='ashby/example')
        text=_format_job(j)
        self.assertIn('Product marketing and GTM ownership.',text)
        self.assertNotIn('$999k',text)
        self.assertNotIn('timezone',text)
        self.assertEqual(3,len(text.splitlines()))

    def test_content_recommendation_does_not_claim_growth_ownership(self):
        j=SimpleNamespace(title='Senior Content Writer (Web3)',company='Example',url='https://example.test/apply')
        text=_format_job(j)
        self.assertIn('Web3 content and product storytelling.',text)
        self.assertNotIn('Growth and GTM ownership.',text)

    def test_untrusted_markup_is_escaped_and_non_http_link_suppressed(self):
        j=SimpleNamespace(title='<script>Growth Lead</script>',company='A&B',url='javascript:alert(1)')
        text=_format_job(j)
        self.assertIn('&lt;script&gt;',text)
        self.assertIn('A&amp;B',text)
        self.assertNotIn('href=',text)
        self.assertNotIn('javascript:',text)

    def test_head_product_growth_startup_scope_does_not_need_literal_early_stage(self):
        desc=DESCRIPTION+' We operate in startup mode with a small, globally distributed team. Own end-to-end product marketing for our product, positioning, GTM, launches and adoption.'
        d=classify_job(job(title='Head of Product Marketing & Growth',description=desc),now=NOW)
        self.assertEqual('qualified',d.status,d.reason)

    def test_ownership_alone_does_not_approve_large_corporate_management(self):
        desc=DESCRIPTION+' Own end-to-end product marketing. Manage multiple global teams and manage managers.'
        d=classify_job(job(title='Head of Product Marketing & Growth',description=desc),now=NOW)
        self.assertEqual('review',d.status)

    def test_exact_curated_source_identity_is_required(self):
        d=classify_job(job(source='company/curated',url='https://unrelated.example/jobs/1'),now=NOW)
        self.assertEqual('review',d.status)

if __name__=='__main__': unittest.main()

import base64,html,unittest
from unittest.mock import patch
import httpx
import linkedin_discovery as source
from filters import apply_filters

URL='https://www.linkedin.com/posts/founder_hiring-growth-activity-123456'
def response(text='',status=200):
    return httpx.Response(status,text=text,request=httpx.Request('GET','https://www.bing.com/search'))
def result(url=URL):
    return '<li class="b_algo"><h2><a href="'+html.escape(url,quote=True)+'">We are hiring a Growth Marketing Manager</a></h2><div class="b_caption"><p>Remote worldwide; posted today.</p></div></li>'

class LinkedInDiscoveryTests(unittest.TestCase):
    def test_actual_posts_and_feed_urls_only(self):
        for url in (URL, 'https://linkedin.com/feed/update/urn:li:activity:123456/'):
            self.assertTrue(source.post_url(url+'?utm_source=test'))
        for url in ('https://www.linkedin.com/jobs/view/123','https://www.linkedin.com/company/example','https://www.linkedin.com/in/founder','https://www.linkedin.com.evil.test/posts/hiring','https://evil.test@www.linkedin.com/posts/hiring','javascript:'+URL,URL+'\\bad','https://www.linkedin.com:8080/posts/hiring'):
            self.assertEqual('',source.post_url(url),url)

    def test_bing_wrapper_decoded_once_without_visiting_linkedin(self):
        encoded=base64.urlsafe_b64encode(URL.encode()).decode().rstrip('=')
        wrapper='https://www.bing.com/ck/a?u=a1'+encoded
        with patch('boards._get',side_effect=AssertionError('No network')):
            self.assertEqual(URL,source.post_url(wrapper))
            self.assertEqual('',source.post_url(wrapper.replace('www.bing.com','bing.com.evil.test')))
            self.assertEqual('',source.post_url('https://www.bing.com/ck/a?u=a1%%%'))

    def test_daily_discovery_bounded_and_snippets_never_prove_eligibility(self):
        with patch('boards._get',return_value=response(result())) as get:
            jobs=source.fetch_linkedin_posts()
        self.assertEqual(2,get.call_count)
        self.assertEqual(1,len(jobs))
        j=jobs[0]
        self.assertEqual(('','','',False),(j.company,j.location,j.posted,j.current_open_verified))
        self.assertEqual([],apply_filters(jobs))
        for call in get.call_args_list:
            self.assertEqual('https://www.bing.com/search',call.args[0])
            self.assertEqual(15,call.kwargs['timeout'])
            self.assertIn('Month',call.kwargs['params'].values())

    def test_blocks_and_challenges_stop_without_bypass(self):
        for reply in (response(status=403),response(status=429),response('<form id="b_captcha"></form>')):
            with patch('boards._get',return_value=reply) as get:
                self.assertEqual([],source.fetch_linkedin_posts())
                self.assertEqual(1,get.call_count)

    def test_hard_query_and_result_caps(self):
        body=''.join(result(URL+str(i)) for i in range(100))
        with patch.object(source,'QUERIES',['query']*100),patch('boards._get',return_value=response(body)) as get:
            jobs=source.fetch_linkedin_posts()
        self.assertEqual(2,get.call_count)
        self.assertEqual(15,len(jobs))

    def test_network_failure_does_not_break_other_sources(self):
        with patch('boards._get',side_effect=httpx.ReadTimeout('test')):
            self.assertEqual([],source.fetch_linkedin_posts())

    def test_verified_oldest_post_bound_survives_newer_repost(self):
        for url in source.KNOWN_EXISTENCE_BOUNDS:
            with patch('boards._get',return_value=response(result(url))):
                j=source.fetch_linkedin_posts()[0]
            self.assertEqual('2026-09-22',j.published_at_upper_bound)
            self.assertEqual('',j.posted)
            self.assertFalse(j.current_open_verified)

    def test_daily_board_wrapper_registered(self):
        import boards
        self.assertIn(boards.fetch_linkedin_hiring_posts,boards.BOARDS)
        with patch.object(source,'fetch_linkedin_posts',return_value=[]) as fetch:
            self.assertEqual([],boards.fetch_linkedin_hiring_posts())
            fetch.assert_called_once_with()

if __name__=='__main__': unittest.main()

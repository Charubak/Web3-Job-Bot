"""Offline indexed-X discovery fixtures; no X, search, or Telegram requests."""

import base64
import html
import unittest
from unittest.mock import patch

import httpx

import boards
from filters import apply_filters


def response(body="", status=200):
    return httpx.Response(status, text=body,
                          request=httpx.Request("GET", "https://www.bing.com/search"))


def result(url="https://x.com/Recruiter/status/12345", title=None, snippet=None):
    if title is None:
        title = 'Recruiter on X: “Hiring a Senior Product Marketing Manager”'
    if snippet is None:
        snippet = 'Oct 1, 2026 · Remote worldwide. Apply for our web3 growth team.'
    return (f'<li class="b_algo"><h2><a href="{html.escape(url, quote=True)}">'
            f'{html.escape(title)}</a></h2><div class="b_caption">'
            f'<span class="news_dt">Oct 1, 2026</span><p>{html.escape(snippet)}</p>'
            '<time datetime="2026-10-01T00:00:00Z"></time></div></li>')


def bing_wrapper(url, host="www.bing.com"):
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://{host}/ck/a?u=a1{encoded}&ntb=1"


class TwitterPostURLTests(unittest.TestCase):
    def test_exact_hosts_and_status_paths_are_canonicalized(self):
        urls = [
            "https://x.com/Recruiter/status/12345",
            "http://twitter.com/Recruiter/status/12345?utm_source=search#post",
            "https://X.COM/Recruiter/status/12345/?s=20",
            "https://twitter.com/Recruiter/status/12345/photo/1",
            "https://x.com/Recruiter/status/12345/video/1",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(boards._twitter_post_url(url),
                                 "https://x.com/recruiter/status/12345")
        self.assertEqual(boards._twitter_post_url("https://x.com/i/web/status/12345"),
                         "https://x.com/i/web/status/12345")

    def test_spoofed_hosts_profiles_searches_and_invalid_paths_are_rejected(self):
        urls = [
            "https://notx.com/recruiter/status/12345",
            "https://x.com.evil.test/recruiter/status/12345",
            "https://evil.test/twitter.com/recruiter/status/12345",
            "https://evil.test/?next=https://x.com/recruiter/status/12345",
            "https://x.com@evil.test/recruiter/status/12345",
            "https://evil.test@x.com/recruiter/status/12345",
            "https://x.com:9999/recruiter/status/12345",
            "https://[invalid/recruiter/status/12345",
            "https://x.com/recruiter/status/not-a-post-id",
            "https://x.com/recruiter/status/12345/redirect",
            "https://x.com/recruiter",
            "https://x.com/search?q=/recruiter/status/12345",
            "https://x.com/recruiter%2fstatus%2f12345",
            "https://x.com/recruiter/status/１２３４５",
            "https://x.com/recruiter/\nstatus/12345",
            "https://x.com\\evil.test/recruiter/status/12345",
            "ftp://x.com/recruiter/status/12345",
            "javascript:https://x.com/recruiter/status/12345",
            "//x.com/recruiter/status/12345",
            "",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(boards._twitter_post_url(url), "")

    def test_bing_wrappers_are_decoded_once_without_following_redirects(self):
        target = "https://twitter.com/Recruiter/status/12345?s=20"
        with patch("boards._get", side_effect=AssertionError("must not fetch")):
            self.assertEqual(boards._twitter_post_url(bing_wrapper(target)),
                             "https://x.com/recruiter/status/12345")
            for url in [
                bing_wrapper(target, host="www.bing.com.evil.test"),
                bing_wrapper("https://x.com.evil.test/recruiter/status/12345"),
                bing_wrapper(bing_wrapper(target)),
                "https://www.bing.com/ck/a?u=a1%%%",
                "https://www.bing.com/ck/a?u=a1_2JhZA",
                bing_wrapper(target) + "&u=a1other",
            ]:
                with self.subTest(url=url):
                    self.assertEqual(boards._twitter_post_url(url), "")


class TwitterDiscoveryTests(unittest.TestCase):
    def test_queries_target_growth_gtm_pmm_and_senior_scope(self):
        queries = " ".join(boards._TWITTER_QUERIES).lower()
        for term in ("growth", "gtm", "go-to-market", "product marketing", "pmm", "senior"):
            self.assertIn(term, queries)
        for term in ("community", "business development", "events", "content", "brand"):
            self.assertNotIn(term, queries)
        for query in boards._TWITTER_QUERIES:
            self.assertIn("(site:x.com OR site:twitter.com)", query)
            self.assertIn("hiring", query)
        self.assertLessEqual(len(boards._TWITTER_QUERIES), boards._TWITTER_MAX_QUERIES)

    def test_month_window_and_requests_are_bounded(self):
        with patch("boards._get", return_value=response()) as get:
            self.assertEqual(boards.fetch_twitter_bing(), [])
        self.assertEqual(get.call_count, len(boards._TWITTER_QUERIES))
        for call in get.call_args_list:
            self.assertEqual(call.args, ("https://www.bing.com/search",))
            self.assertEqual(call.kwargs["params"]["freshness"], "Month")
            self.assertEqual(call.kwargs["params"]["filters"], 'ex1:"ez3"')
            self.assertEqual(call.kwargs["params"]["count"], boards._TWITTER_RESULTS_PER_QUERY)
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertNotIn("first", call.kwargs["params"])

    def test_expanded_query_list_and_large_search_pages_have_hard_caps(self):
        replies = [response("".join(result(f"https://x.com/hiring/status/{i * 100 + j}")
                                    for j in range(1, 51))) for i in range(10)]
        with patch("boards._TWITTER_QUERIES", ["query"] * 100), \
                patch("boards._get", side_effect=replies) as get:
            jobs = boards.fetch_twitter_bing()
        self.assertEqual(get.call_count, boards._TWITTER_MAX_QUERIES)
        self.assertEqual(len(jobs), boards._TWITTER_MAX_QUERIES * boards._TWITTER_RESULTS_PER_QUERY)

    def test_post_or_index_dates_handles_and_location_text_are_not_job_evidence(self):
        with patch("boards._get", return_value=response(result())):
            jobs = boards.fetch_twitter_bing()
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Hiring a Senior Product Marketing Manager")
        self.assertEqual(job.posted, "")
        self.assertEqual(job.company, "")
        self.assertEqual(job.location, "")
        self.assertEqual(job.employer_country, "")
        self.assertEqual(job.employer_country_source, "")
        self.assertEqual(job.employer_country_verified_at, "")
        self.assertEqual(job.source, "x.com")
        self.assertIn("Oct 1, 2026", job.description)
        self.assertEqual(apply_filters(jobs), [])

    def test_duplicates_wrappers_and_tracking_do_not_create_extra_candidates(self):
        page = "".join(result(url) for url in [
            "https://x.com/Recruiter/status/12345",
            "https://twitter.com/recruiter/status/12345?s=20",
            bing_wrapper("https://x.com/recruiter/status/12345"),
            "https://x.com.evil.test/recruiter/status/67890",
            "https://x.com/recruiter",
        ])
        with patch("boards._get", return_value=response(page)) as get:
            jobs = boards.fetch_twitter_bing()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].url, "https://x.com/recruiter/status/12345")
        self.assertEqual(get.call_count, len(boards._TWITTER_QUERIES))

    def test_missing_title_and_malformed_results_do_not_break_other_results(self):
        page = ('<li class="b_algo"><p>No heading</p></li>'
                + result(title="", snippet="") + result())
        with patch("boards._get", return_value=response(page)):
            jobs = boards.fetch_twitter_bing()
        self.assertEqual(len(jobs), 1)

    def test_denied_rate_limited_or_captcha_search_stops_without_retry(self):
        cases = [response(status=403), response(status=429),
                 response('<form id="b_captcha" action="/challenge">Verify</form>')]
        for reply in cases:
            with self.subTest(status=reply.status_code, body=reply.text):
                with patch("boards._get", return_value=reply) as get:
                    self.assertEqual(boards.fetch_twitter_bing(), [])
                self.assertEqual(get.call_count, 1)

    def test_zero_results_and_transient_errors_are_graceful(self):
        for reply in (response("<p>No results found</p>"), response(status=503)):
            with patch("boards._get", return_value=reply) as get:
                self.assertEqual(boards.fetch_twitter_bing(), [])
            self.assertEqual(get.call_count, len(boards._TWITTER_QUERIES))
        with patch("boards._get", side_effect=[TimeoutError("timeout")]
                   + [response(result())] * (len(boards._TWITTER_QUERIES) - 1)):
            self.assertEqual(len(boards.fetch_twitter_bing()), 1)


if __name__ == "__main__":
    unittest.main()

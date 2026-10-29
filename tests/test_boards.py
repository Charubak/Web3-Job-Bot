"""Offline adapter/resource-limit regressions; never contact boards or Telegram."""

import gzip
import hashlib
import threading
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

import boards


def response(payload=None, text="", status=200):
    kwargs = {"json": payload} if payload is not None else {"text": text}
    return httpx.Response(status, request=httpx.Request("GET", "https://example.test/jobs"), **kwargs)


def job(source="aggregator", job_id="one", location="", description="", company="Example"):
    return boards.Job(job_id, "Growth Marketing Manager", company, location,
                      f"https://example.test/jobs/{job_id}", source, description=description)


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


class BoundedFetchTests(unittest.TestCase):
    def client_for(self, reply):
        return httpx.Client(transport=httpx.MockTransport(lambda request: reply))

    def test_body_is_limited_while_streaming_and_closed(self):
        stream = ChunkStream([b"123", b"456"])
        reply = httpx.Response(200, stream=stream)
        client = self.client_for(reply)
        with patch("boards.httpx.Client", return_value=client), patch("boards.MAX_RESPONSE_BYTES", 5):
            with self.assertRaisesRegex(ValueError, "exceeded 5 bytes"):
                boards._get("https://example.test/large")
        self.assertTrue(stream.closed)

    def test_limit_is_on_decoded_not_compressed_bytes(self):
        stream = ChunkStream([gzip.compress(b"x" * 1000)])
        reply = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)
        client = self.client_for(reply)
        with patch("boards.httpx.Client", return_value=client), patch("boards.MAX_RESPONSE_BYTES", 100):
            with self.assertRaises(ValueError):
                boards._get("https://example.test/compressed")
        self.assertTrue(stream.closed)

    def test_compressed_success_is_not_decoded_twice(self):
        reply = httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/json"},
                               stream=ChunkStream([gzip.compress(b'{"jobs": []}')]))
        client = self.client_for(reply)
        with patch("boards.httpx.Client", return_value=client):
            result = boards._get("https://example.test/compressed")
        self.assertEqual(result.json(), {"jobs": []})
        self.assertNotIn("content-encoding", result.headers)

    def test_total_body_deadline(self):
        stream = ChunkStream([b"abc"])
        client = self.client_for(httpx.Response(200, stream=stream))
        fake_time = Mock()
        fake_time.monotonic.side_effect = [0, boards.MAX_REQUEST_SECONDS + 1]
        with patch("boards.httpx.Client", return_value=client), patch("boards.time", fake_time):
            with self.assertRaises(TimeoutError):
                boards._get("https://example.test/slow")
        self.assertTrue(stream.closed)

    def test_feedparser_gets_bounded_bytes_not_a_url(self):
        rss = b'<rss version="2.0"><channel><title>Jobs</title><item><title>Growth Marketing Manager at Example</title><link>https://example.test/job</link></item></channel></rss>'
        reply = httpx.Response(200, content=rss, request=httpx.Request("GET", "https://example.test/rss"))
        with patch("boards._get", return_value=reply) as get, patch("boards.feedparser.parse", wraps=boards.feedparser.parse) as parse:
            result = boards._fetch_feed("https://example.test/rss")
        get.assert_called_once_with("https://example.test/rss", follow_redirects=True, timeout=15)
        parse.assert_called_once_with(rss)
        self.assertEqual(len(result.entries), 1)

    def test_feed_http_error_is_not_parsed(self):
        with patch("boards._get", return_value=response(status=503)), patch("boards.feedparser.parse") as parse:
            self.assertEqual(boards.fetch_remote3(), [])
        parse.assert_not_called()

    def test_board_concurrency_has_a_single_small_limit(self):
        lock = threading.Lock()
        active = peak = 0

        def fetch():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return []

        with patch("boards.BOARDS", [fetch] * 6):
            self.assertEqual(boards.fetch_all(), [])
        self.assertLessEqual(peak, boards.MAX_BOARD_WORKERS)
        self.assertEqual(boards.MAX_BOARD_WORKERS, 2)

    def test_ats_companies_do_not_create_nested_pools(self):
        with patch("boards.GREENHOUSE_COMPANIES", [("one", "One"), ("two", "Two")]), \
                patch("boards._fetch_greenhouse_one", side_effect=[[job(job_id="1")], [job(job_id="2")]]) as fetch, \
                patch("concurrent.futures.ThreadPoolExecutor", side_effect=AssertionError("nested pool")):
            self.assertEqual(len(boards.fetch_greenhouse()), 2)
        self.assertEqual(fetch.call_count, 2)


class EvidencePreservationTests(unittest.TestCase):
    def test_job_country_metadata_is_optional(self):
        posting = job()
        self.assertEqual(posting.employer_country, "")
        self.assertEqual(posting.employer_country_source, "")
        self.assertEqual(posting.employer_country_verified_at, "")

    def test_greenhouse_requests_content_and_preserves_residency(self):
        payload = {"jobs": [{"absolute_url": "https://careers.example.test/123", "title": "Growth &amp; Marketing Manager",
                              "location": {"name": "Remote"}, "first_published": "2026-10-18T10:00:00Z",
                              "content": "&lt;p&gt;We hire globally.&lt;/p&gt;&lt;p&gt;Must reside in Canada.&lt;/p&gt;"}]}
        with patch("boards._get", return_value=response(payload)) as get:
            jobs = boards._fetch_greenhouse_one("example", "Example")
        self.assertEqual(get.call_args.kwargs["params"], {"content": "true"})
        self.assertEqual(jobs[0].source, "greenhouse/example")
        self.assertEqual(jobs[0].location, "Remote")
        self.assertEqual(jobs[0].description, "We hire globally.\nMust reside in Canada.")
        self.assertEqual(jobs[0].posted, "2026-10-18T10:00:00Z")

    def test_greenhouse_updated_timestamp_never_refreshes_original_age(self):
        for first_published in ("2024-01-01T10:00:00Z", "", None):
            with self.subTest(first_published=first_published):
                payload = {"jobs": [{"absolute_url": "https://careers.example.test/123",
                                      "title": "Growth Marketing Manager",
                                      "first_published": first_published,
                                      "updated_at": "2026-10-19T10:00:00Z"}]}
                with patch("boards._get", return_value=response(payload)):
                    posting = boards._fetch_greenhouse_one("example", "Example")[0]
                self.assertEqual(posting.posted, first_published or "")

    def lever_posting(self):
        return {"hostedUrl": "https://jobs.lever.co/example/123", "text": "Growth Marketing Manager",
                "categories": {"location": "Remote", "allLocations": ["Remote", "Canada"]},
                "workplaceType": "remote", "createdAt": 1792317600000,
                "opening": "<p>Join us.</p>", "descriptionPlain": "Global product marketing.",
                "lists": [{"text": "Requirements", "content": "<ul><li>Must be based in Canada.</li></ul>"}],
                "additional": "<p>Local work authorization required.</p>"}

    def test_lever_preserves_locations_and_all_requirement_sections(self):
        with patch("boards._get", return_value=response([self.lever_posting()])):
            posting = boards._fetch_lever_one("example", "Example")[0]
        self.assertIn("Canada", posting.location)
        self.assertIn("remote", posting.location)
        for text in ("Join us.", "Global product marketing.", "Requirements", "Must be based in Canada.", "Local work authorization required."):
            self.assertIn(text, posting.description)
        self.assertEqual(posting.posted, "1792317600000")

    def test_lever_updated_timestamp_never_substitutes_for_creation(self):
        for fetch in (lambda: boards._fetch_lever_one("example", "Example"), boards.fetch_lever_aave):
            for created in (1704189600000, "", None):
                with self.subTest(fetch=fetch, created=created):
                    payload = self.lever_posting()
                    payload.update(createdAt=created, updatedAt=1792404000000)
                    with patch("boards._get", return_value=response([payload])):
                        posting = fetch()[0]
                    self.assertEqual(posting.posted, str(created or ""))

    def test_eu_lever_uses_the_same_complete_description(self):
        with patch("boards._get", return_value=response([self.lever_posting()])):
            posting = boards.fetch_lever_aave()[0]
        self.assertEqual(posting.source, "lever/aave")
        self.assertIn("Must be based in Canada.", posting.description)
        self.assertIn("Local work authorization required.", posting.description)
        self.assertIn("Canada", posting.location)

    def test_lever_preserves_hybrid_status_even_with_remote_location(self):
        self.assertEqual(boards._lever_location({"categories": {"location": "Remote"}, "workplaceType": "hybrid"}), "Remote; hybrid")

    def test_global_marketing_copy_never_becomes_a_location(self):
        for summary in ("We are a global company. Must be based in the US.",
                        "Join our worldwide community and distributed team.",
                        "Remote work is changing the world."):
            self.assertEqual(boards._extract_location_from_summary(summary), "")

    def test_explicit_rss_location_keeps_geo_qualifiers(self):
        self.assertEqual(boards._extract_location_from_summary("<p>Location: Remote - US only</p><p>We hire globally.</p>"), "Remote - US only")

    def test_inline_html_location_keeps_restrictions(self):
        self.assertEqual(boards._extract_location_from_summary("<p>Location: <span>Remote</span> - <strong>US only</strong></p><p>Global team</p>"), "Remote  -  US only")

    def test_rss_full_content_restrictions_are_retained(self):
        feed = Mock(entries=[{"title": "Growth Marketing Manager at Example", "link": "https://example.test/job",
                              "summary": "Global product marketing.",
                              "content": [{"value": "<p>Must live in the United States.</p>"}]}])
        with patch("boards._fetch_feed", return_value=feed):
            posting = boards.fetch_cryptocurrencyjobs()[0]
        self.assertEqual(posting.location, "")
        self.assertIn("Must live in the United States.", posting.description)

    def test_remote3_keeps_country_only_and_qualified_remote_locations(self):
        for summary, expected in (("at Example - Full-time - Canada - $120000", "Canada"),
                                  ("at Example - Full-time - Remote - US - $120000", "Remote - US"),
                                  ("We are a global team", "")):
            with self.subTest(summary=summary):
                feed = Mock(entries=[{"title": "Growth Marketing Manager at Example", "link": "https://example.test/job", "summary": summary}])
                with patch("boards._fetch_feed", return_value=feed):
                    posting = boards.fetch_remote3()[0]
                self.assertEqual(posting.location, expected)
                self.assertIn(summary, posting.description)

    def test_remoteok_missing_location_stays_unknown_and_description_survives(self):
        payload = [{"position": "Growth Marketing Manager", "company": "Example", "url": "https://example.test/job",
                    "description": "<p>Must reside in the US.</p>"}]
        with patch("boards._get", return_value=response(payload)):
            posting = boards.fetch_remoteok()[0]
        self.assertEqual(posting.location, "")
        self.assertIn("Must reside in the US.", posting.description)

    def test_remotive_preserves_description(self):
        payload = {"jobs": [{"title": "Web3 Growth Marketing Manager", "company_name": "Example", "url": "https://example.test/job",
                              "candidate_required_location": "Worldwide", "description": "<p>Must be based in Canada.</p>"}]}
        with patch("boards._get", return_value=response(payload)):
            posting = boards.fetch_remotive()[0]
        self.assertEqual(posting.location, "Worldwide")
        self.assertIn("Must be based in Canada.", posting.description)

    def test_web3career_keeps_all_location_tags(self):
        page = '''<table><tr onclick="tableTurboRowClick(event, '/growth-job/1')"><td><h2>Growth Marketing Manager</h2></td><td><h3>Example</h3></td><td><a href="/web3-jobs-remote">Remote</a><a href="/web3-jobs-canada">Canada</a></td></tr></table>'''
        with patch("boards._get", return_value=response(text=page)):
            posting = boards._scrape_web3career_page("/marketing-jobs")[0]
        self.assertEqual(posting.location, "Remote; Canada")
        self.assertIn("Canada", posting.description)

    def test_web3career_current_layout_preserves_company_location_and_exact_date(self):
        # Current live layout: the first cell contains company and location,
        # while the second is an age. Previously this made company="3d".
        page = '''<table><tr onclick="tableTurboRowClick(event, '/marketing-growth-lead-hylo/155526')">
          <td class="cell-main"><h2>Marketing Growth Lead</h2>
            <div class="job-company-location"><h3>Hylo</h3>
              <span class="job-location-mobile"><span>📍</span>
                <span>Remote - US only</span></span></div></td>
          <td class="cell-posted"><time datetime="2026-10-16 09:10:51+07:00">3d</time></td>
          <td class="cell-salary">$102k - $110k</td></tr></table>'''
        with patch("boards._get", return_value=response(text=page)):
            posting = boards._scrape_web3career_page("/marketing-jobs")[0]
        self.assertEqual(posting.title, "Marketing Growth Lead")
        self.assertEqual(posting.company, "Hylo")
        self.assertEqual(posting.location, "Remote - US only")
        self.assertEqual(posting.posted, "2026-10-16 09:10:51+07:00")

    def test_web3career_current_location_keeps_city_state_country(self):
        page = '''<table><tr onclick="tableTurboRowClick(event, '/marketing/1')">
          <td><h2>Marketing Lead</h2><h3>Example</h3>
            <span class="job-location-mobile"><span>📍</span>
              <a href="/web3-jobs-san-francisco">San Francisco</a><span>,</span>
              <a href="/web3-jobs-california">CA</a><span>,</span>
              <a href="/web3-jobs-united-states">United States</a></span></td>
          <td><time>6d</time></td></tr></table>'''
        with patch("boards._get", return_value=response(text=page)):
            posting = boards._scrape_web3career_page("/marketing-jobs")[0]
        self.assertEqual(posting.location, "San Francisco, CA, United States")

    def test_web3career_never_calls_a_relative_age_a_company(self):
        for cell in ("3d", "6d", "10 days ago", "Today", "<time>6d</time>"):
            with self.subTest(cell=cell):
                page = f'''<table><tr onclick="tableTurboRowClick(event, '/marketing/1')">
                  <td><h2>Marketing Lead</h2></td><td>{cell}</td></tr></table>'''
                with patch("boards._get", return_value=response(text=page)):
                    posting = boards._scrape_web3career_page("/marketing-jobs")[0]
                self.assertEqual(posting.company, "")

    def test_telegram_post_residency_text_is_preserved(self):
        page = '''<div class="tgme_widget_message_wrap"><div class="tgme_widget_message_text">Example is hiring<br/>Growth Marketing Manager<br/>Must be based in the US.<br/><a href="https://example.test/job">Apply</a></div><time datetime="2026-10-18T10:00:00Z"></time></div>'''
        with patch("boards._get", return_value=response(text=page)):
            posting = boards.fetch_web3hiring_telegram()[0]
        self.assertIn("Must be based in the US.", posting.description)
        self.assertEqual(posting.location, "")
        self.assertEqual(posting.posted, "2026-10-18T10:00:00Z")


class CanonicalIdentityTests(unittest.TestCase):
    def test_canonical_urls_keep_their_existing_id(self):
        for url in ("https://jobs.lever.co/example/123", "https://example.test/jobs?jobId=123&lang=en",
                    "https://example.test/?position=Growth%20Lead", "https://example.test/#/jobs/123"):
            with self.subTest(url=url):
                self.assertEqual(boards.canonical_job_url(url), url)
                self.assertEqual(boards._make_id(url), hashlib.sha256(url.encode()).hexdigest()[:16])

    def test_tracking_and_page_anchors_do_not_create_new_jobs(self):
        base = "https://jobs.lever.co/example/123"
        for suffix in ("?utm_source=telegram&utm_campaign=daily#apply", "?lever-source=board",
                       "?gh_src=123&fbclid=ad", "#details"):
            with self.subTest(suffix=suffix):
                self.assertEqual(boards.canonical_job_url(base + suffix), base)
                self.assertEqual(boards._make_id(base + suffix), boards._make_id(base))

    def test_job_query_fields_are_kept_when_tracking_is_removed(self):
        url = "https://example.test/jobs?utm_source=feed&jobId=123&lang=en&gclid=ad"
        self.assertEqual(boards.canonical_job_url(url), "https://example.test/jobs?jobId=123&lang=en")
        self.assertNotEqual(boards._make_id(url), boards._make_id(url.replace("jobId=123", "jobId=456")))

    def test_unknown_query_keys_encoding_and_duplicate_order_are_preserved(self):
        url = "https://example.test/jobs?ref=123&source=jobs&job=A%2FB&job=C%20D"
        self.assertEqual(boards.canonical_job_url(url + "&utm_medium=message#apply"), url)

    def test_identity_bearing_fragments_remain_distinct(self):
        for fragment in ("#/jobs/123", "#!/jobs/123", "#jobId=123", "#job-123"):
            with self.subTest(fragment=fragment):
                url = "https://example.test/" + fragment
                self.assertEqual(boards.canonical_job_url(url), url)
                self.assertNotEqual(boards._make_id(url), boards._make_id(url.replace("123", "456")))

    def test_scheme_and_hostname_case_are_normalized(self):
        self.assertEqual(boards.canonical_job_url("HTTPS://EXAMPLE.TEST/Jobs?job=ABC"),
                         "https://example.test/Jobs?job=ABC")


class DuplicateEvidenceTests(unittest.TestCase):
    def eligible_posting(self, days, job_id="same"):
        now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        posting = job(source="lever/example", job_id=job_id, location="Remote - Worldwide",
                      description="Own growth strategy, acquisition and retention for our protocol. "
                                  "Build experiments with product and analytics, define messaging and lead "
                                  "cross-functional product launches. We need an experienced hands-on marketer "
                                  "with 6+ years of marketing experience.")
        posting.title = "Senior Growth Marketing Manager"
        posting.posted = (now - timedelta(days=days)).isoformat()
        posting.employer_country = "United Kingdom"
        posting.employer_country_source = "https://example.test/about"
        posting.employer_country_verified_at = "2026-10-18"
        return posting, now

    def test_fetch_history_and_filter_keep_expired_same_job_in_either_order(self):
        import storage
        from filters import classify_job

        for oldest_first in (False, True):
            recent, now = self.eligible_posting(20)
            old, _ = self.eligible_posting(31)
            # Tracking URLs must match the same job's canonical identity.
            old.url += "?utm_source=repost#apply"
            recent.id = boards._make_id(recent.url)
            old.id = boards._make_id(old.url)
            records = [old, recent] if oldest_first else [recent, old]
            with self.subTest(oldest_first=oldest_first), tempfile.TemporaryDirectory() as tmp:
                with patch("boards.BOARDS", [lambda: records]), \
                        patch("storage.DB_PATH", Path(tmp) / "history.db"):
                    fetched = boards.fetch_all(now=now)
                    self.assertEqual(len(fetched), 1)
                    reconciled = storage.reconcile_job_history(fetched, persist=True, now=now)
                    decision = classify_job(reconciled[0], now=now)
                    self.assertEqual(decision.status, "rejected", decision.reason)
                    self.assertIn("older than 30 days", decision.reason)
                    resurfaced = storage.reconcile_job_history([recent], now=now)
                    self.assertEqual(classify_job(resurfaced[0], now=now).status, "rejected")
            self.assertEqual(recent.posted, (now - timedelta(days=20)).isoformat())

    def test_distinct_requisition_dates_do_not_merge_on_title_and_company(self):
        from filters import classify_job

        recent, now = self.eligible_posting(20, "new-requisition")
        old, _ = self.eligible_posting(31, "old-requisition")
        with patch("boards.BOARDS", [lambda: [recent, old]]):
            fetched = boards.fetch_all(now=now)
        self.assertEqual(len(fetched), 2)
        self.assertEqual(classify_job(fetched[0], now=now).status, "qualified")
        self.assertEqual(classify_job(fetched[1], now=now).status, "rejected")

    def test_aggregator_date_cannot_replace_same_url_primary_date(self):
        primary, now = self.eligible_posting(20)
        repost, _ = self.eligible_posting(31)
        repost.source = "aggregator"
        repost.description = "Aggregator description"
        with patch("boards.BOARDS", [lambda: [repost, primary]]):
            fetched = boards.fetch_all(now=now)
        self.assertEqual(fetched, [primary])

    def test_aggregator_date_cannot_fill_missing_primary_date(self):
        from filters import classify_job

        primary, now = self.eligible_posting(20)
        primary.posted = ""
        repost, _ = self.eligible_posting(20)
        repost.source = "aggregator"
        repost.description = "Aggregator description"
        with patch("boards.BOARDS", [lambda: [repost, primary]]):
            fetched = boards.fetch_all(now=now)
        self.assertEqual(fetched, [primary])
        self.assertEqual(classify_job(fetched[0], now=now).status, "review")

    def test_missing_or_future_primary_date_stays_review_when_duplicates_are_unexpired(self):
        from filters import classify_job

        for missing in ("", "invalid", "2026-10-20T12:00:00+00:00"):
            unknown, now = self.eligible_posting(20)
            unknown.posted = missing
            known, _ = self.eligible_posting(20)
            with self.subTest(posted=missing), patch("boards.BOARDS", [lambda: [unknown, known]]):
                fetched = boards.fetch_all(now=now)
            self.assertEqual(fetched[0].posted, missing)
            self.assertEqual(classify_job(fetched[0], now=now).status, "review")

    def test_expired_primary_duplicate_wins_even_when_preferred_date_is_unknown(self):
        from filters import classify_job

        unknown, now = self.eligible_posting(20)
        unknown.posted = ""
        expired, _ = self.eligible_posting(31)
        with patch("boards.BOARDS", [lambda: [unknown, expired]]):
            fetched = boards.fetch_all(now=now)
        self.assertEqual(classify_job(fetched[0], now=now).status, "rejected")

    def test_primary_replaces_earlier_low_information_aggregator(self):
        repost = job(job_id="repost", location="Worldwide")
        primary = job(source="greenhouse/example", job_id="primary", location="Remote", description="Must be based in the US.")
        with patch("boards.BOARDS", [lambda: [repost], lambda: [primary]]):
            self.assertEqual(boards.fetch_all(), [primary])

    def test_primary_wins_identical_url_duplicate(self):
        repost = job(job_id="same", location="Worldwide")
        primary = job(source="lever/example", job_id="same", location="Remote - US", description="US only.")
        with patch("boards.BOARDS", [lambda: [repost], lambda: [primary]]):
            self.assertEqual(boards.fetch_all(), [primary])

    def test_distinct_primary_requisitions_keep_their_eligibility(self):
        us = job(source="lever/example", job_id="us", location="Remote - US", description="US only.")
        global_job = job(source="lever/example", job_id="global", location="Worldwide", description="Global hiring.")
        with patch("boards.BOARDS", [lambda: [us, global_job]]):
            self.assertEqual(boards.fetch_all(), [us, global_job])

    def test_unknown_companies_do_not_collapse_unrelated_jobs(self):
        first, second = job(job_id="1", company=""), job(job_id="2", company="")
        with patch("boards.BOARDS", [lambda: [first, second]]):
            self.assertEqual(boards.fetch_all(), [first, second])

    def test_board_failure_does_not_cancel_other_results(self):
        def broken():
            raise ValueError("test failure")

        good = job()
        with patch("boards.BOARDS", [broken, lambda: [good]]):
            self.assertEqual(boards.fetch_all(), [good])


if __name__ == "__main__":
    unittest.main()

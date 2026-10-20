"""Synthetic DOM regressions, separate from the live-HTML smoke verification.

Only the page's structural labels/selectors follow inspected public HTML.
The compact test descriptions below are synthetic, not raw live DOM captures.
"""

import json
import re
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

import boards
import company_roles as curated
from filters import classify_job


NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
FORM = '''<form><fieldset><input id="apply-name" required>
<input id="apply-email" type="email" required><input type="file" id="resume">
<button type="submit" disabled>Submit application →</button></fieldset></form>'''
TEXT = '''The successful candidate owns product positioning, acquisition strategy,
activation, retention and the measurement of growth initiatives for a Web3
product. Work with product and engineering teams to understand customer needs
and create useful messaging. Plan feature launches, develop campaigns, evaluate
distribution channels and improve onboarding based on research and product data.
Build repeatable marketing processes and report clearly on performance. Bring
experience leading marketing programs, strong written communication, analytical
judgment and independent execution. You will set priorities, test hypotheses,
manage resources and translate technical information into clear product stories.
These responsibilities and requirements describe a complete synthetic role used
to test extraction; they are not a copied employer job description.'''


def role_page(source, *, location="Remote", extra="", form=FORM):
    heading = f'<h1>{source.title}</h1>'
    if source.url == curated.DEFUSE_URL:
        heading += f'<div class="job__location">{location}</div>'
    elif source.url == curated.PANCAKESWAP_URL:
        heading += f'<p>Pancakeswap · {location} · Director</p>'
    else:
        heading += f'<dl><dt>Location</dt><dd>{location}</dd></dl>'
    sections = ''.join(f'<h2>{label}</h2><p>{TEXT}</p>' for label in source.sections)
    action = form if source.url in (curated.DEFUSE_URL, curated.PANCAKESWAP_URL) else (
        '<a href="mailto:careers@intercepta.io">Apply for this role</a>')
    return (f'<html><head><title>{source.company} Careers</title></head><body>'
            f'<nav>Navigation</nav><main>{heading}<p>{source.company}</p>'
            f'{sections}{extra}{action}</main><footer>Other jobs</footer></body></html>')


def index_page(*, target_body=True, extra="", other="United States only", target_closed=""):
    source = curated.ROLE_SOURCES[3]
    body = (f'<h4>Responsibilities</h4><p>{TEXT}</p><h4>Requirements</h4>'
            '<p>Senior marketing experience and clear writing.</p>') if target_body else ''
    return (f'<html><head><title>{source.company} careers</title></head><body><main>'
            '<h1>Careers</h1><h2>Open roles</h2><article><div>'
            f'<h3>{source.title}</h3></div><p>Remote Full-time Growth</p>'
            '<a href="mailto:careers@thegroupthree.com">Apply →</a>'
            f'{body}{extra}{target_closed}</article><article><h3>Community Lead</h3>'
            f'<p>{other}</p><h4>Requirements</h4><p>{TEXT}</p>'
            '<a href="mailto:careers@thegroupthree.com">Apply →</a></article>'
            '<h2>Do not see your role?</h2><p>Generic applicant information</p>'
            '</main></body></html>')


def response(page, source, *, status=200, url=None):
    return httpx.Response(status, text=page, request=httpx.Request("GET", url or source.url))


def schema(source, **changes):
    posting = {"@type": "JobPosting", "title": source.title, "url": source.url,
               "hiringOrganization": {"@type": "Organization", "name": source.company}}
    posting.update(changes)
    return '<script type="application/ld+json">' + json.dumps(posting) + '</script>'


class CuratedRoleTests(unittest.TestCase):
    def fetch(self, source, page=None, **kwargs):
        with patch('boards._get', return_value=response(page or role_page(source), source, **kwargs)):
            return curated._fetch_one(source)

    def test_exact_identity_one_shared_bounded_request_and_unknown_date(self):
        for source in curated.ROLE_SOURCES[:3]:
            with self.subTest(company=source.company):
                with patch('boards._get', return_value=response(role_page(source), source)) as get:
                    job = curated._fetch_one(source)[0]
                get.assert_called_once_with(source.url, follow_redirects=True, timeout=12)
                self.assertEqual(job.company, source.company)
                self.assertEqual(job.title, source.title)
                self.assertEqual(job.id, boards._make_id(source.url))
                self.assertEqual(job.url, source.url)
                self.assertEqual(job.source, 'company/curated')
                self.assertEqual(job.posted, '')
                self.assertEqual(job.published_at_upper_bound, curated.KNOWN_ROLE_EXISTENCE.get(job.url, ''))
                self.assertEqual(job.employer_country, '')
                self.assertTrue(job.current_open_verified)
                self.assertIn(TEXT.splitlines()[0], job.description)

    def test_full_body_header_and_end_restrictions_are_retained(self):
        for source in curated.ROLE_SOURCES[:3]:
            page = role_page(source, location='Remote · United States only', extra=(
                '<section>Must reside in Canada. Must have US work authorization. '
                'Mandatory UTC-5 overlap. This is a hybrid position.</section>'))
            job = self.fetch(source, page)[0]
            self.assertIn('United States only', job.location)
            for text in ('Must reside in Canada', 'US work authorization', 'UTC-5', 'hybrid position'):
                self.assertIn(text, job.description)
            self.assertIn('Requirements', job.description)

    def test_bare_remote_never_invents_global_eligibility(self):
        for source in curated.ROLE_SOURCES[:3]:
            job = self.fetch(source)[0]
            self.assertIn('Remote', job.location)
            self.assertNotIn('worldwide', job.location.lower())
            self.assertNotIn('india eligible', job.description.lower())
            missing = self.fetch(source, role_page(source, location=''))[0]
            self.assertNotIn('Remote', missing.location)

    def test_disabled_submit_for_blank_application_is_not_closure(self):
        for source in curated.ROLE_SOURCES[:2]:
            self.assertEqual(len(self.fetch(source)), 1)
            enabled = role_page(source, form=FORM.replace(' disabled', ''))
            self.assertEqual(len(self.fetch(source, enabled)), 1)

    def test_incomplete_hidden_or_wholly_disabled_forms_are_rejected(self):
        for source in curated.ROLE_SOURCES[:2]:
            for form in ('<button>Apply</button>', '', FORM.replace('<form>', '<form hidden>'),
                         FORM.replace('<fieldset>', '<fieldset disabled>'),
                         FORM.replace('<form>', '<form aria-disabled="true">'),
                         FORM.replace('id="apply-email" type="email"', 'id="other"'),
                         FORM.replace('<input type="file" id="resume">', ''),
                         FORM.replace('Submit application →', 'Subscribe to updates')):
                with self.subTest(company=source.company, form=form[:70]):
                    self.assertEqual(self.fetch(source, role_page(source, form=form)), [])

    def test_cloudflare_email_application_link_is_valid_but_not_naked_endpoint(self):
        source = curated.ROLE_SOURCES[2]
        original = 'mailto:careers@intercepta.io'
        for href in ('/cdn-cgi/l/email-protection#ea898b988f8f9899aa83849e8f98898f9a9e8bc48385',
                     'mailto:careers@intercepta.io?subject=Marketing%20Lead'):
            self.assertEqual(len(self.fetch(source, role_page(source).replace(original, href))), 1)
        for href in ('', '#', '/cdn-cgi/l/email-protection', 'javascript:alert(1)',
                     'https://evil.test/cdn-cgi/l/email-protection#ea898b988f8f9899'):
            self.assertEqual(self.fetch(source, role_page(source).replace(original, href)), [])

    def test_hidden_or_disabled_apply_links_cannot_verify_open(self):
        source = curated.ROLE_SOURCES[2]
        for attr in ('hidden', 'aria-disabled="true"', 'style="display:none"'):
            self.assertEqual(self.fetch(source, role_page(source).replace('<a href=', f'<a {attr} href=')), [])

    def test_closure_overrides_complete_description_and_application(self):
        for source in curated.ROLE_SOURCES[:3]:
            for closure in ('This position has been filled.', 'This role is closed.',
                            'Applications are closed.', 'We are no longer accepting applications.'):
                self.assertEqual(self.fetch(source, role_page(source, extra=f'<p>{closure}</p>')), [])

    def test_generic_missing_identity_missing_sections_and_empty_shell_rejected(self):
        for source in curated.ROLE_SOURCES[:3]:
            pages = ['Access denied', '<html><div id="root"></div><script>renderJob()</script></html>',
                     f'<h1>{source.title}</h1><p>{source.company}</p>{FORM}',
                     re.sub(re.escape(source.company), 'Other Company', role_page(source), flags=re.I),
                     role_page(source).replace(source.title, 'Unrelated Manager'),
                     role_page(source).replace(source.sections[-1], 'Generic information')]
            for page in pages:
                self.assertEqual(self.fetch(source, page), [])

    def test_http_error_redirect_wrong_host_or_requisition_rejected(self):
        source = curated.ROLE_SOURCES[0]
        for status in (301, 403, 404, 410, 429, 500):
            self.assertEqual(self.fetch(source, status=status), [])
        for url in ('https://job-boards.eu.greenhouse.io/defuselabs',
                    source.url.replace('4997160101', '999999'),
                    source.url.replace('greenhouse.io', 'greenhouse.io.evil.test'),
                    source.url.replace('https://', 'https://attacker@'),
                    source.url.replace('https://', 'http://')):
            self.assertEqual(self.fetch(source, url=url), [])

    def test_http_failure_has_no_cached_or_static_role_fallback(self):
        with patch('boards._get', side_effect=httpx.ReadTimeout('timeout')) as get:
            self.assertEqual(curated.fetch_company_roles(), [])
        self.assertEqual(get.call_count, len(curated.ROLE_SOURCES) + 1)

    def test_original_date_only_matching_employer_jobposting_dateposted(self):
        source = curated.ROLE_SOURCES[0]
        for stamp, expected in (('2026-10-03', '2026-10-03'),
                                ('2026-10-03T12:30:00Z', '2026-10-03T12:30:00Z'),
                                ('yesterday', ''), ('2026-02-30', ''), ('', '')):
            page = role_page(source) + schema(source, datePosted=stamp)
            self.assertEqual(self.fetch(source, page)[0].posted, expected)
        page = role_page(source, extra='<p>Updated today. Posted 21 hours ago.</p>')
        page += schema(source, updatedAt='2026-10-19', publishedAt='2026-10-19')
        self.assertEqual(self.fetch(source, page)[0].posted, '')
        # An investor portfolio board's structured date is not asserted to be
        # the employer's original publication date.
        source = curated.ROLE_SOURCES[1]
        self.assertEqual(self.fetch(source, role_page(source) + schema(source, datePosted='2026-10-19'))[0].posted, '')

    def test_old_investor_schema_date_is_retained_as_exclusion_only_bound(self):
        source = curated.ROLE_SOURCES[1]
        job = self.fetch(source, role_page(source) + schema(source, datePosted='2026-08-01T13:00:00Z'))[0]
        self.assertEqual(job.posted, '')
        self.assertEqual(job.published_at_upper_bound, '2026-08-01T13:00:00Z')
        decision = classify_job(job, now=NOW)
        self.assertEqual(decision.status, 'rejected')
        self.assertTrue(any('older than' in reason for reason in decision.reasons))

    def test_future_time_and_timezone_survive_for_classifier_review(self):
        source = curated.ROLE_SOURCES[0]
        for stamp in ('2026-10-19T23:59:00Z', '2026-10-19T18:00:00-07:00'):
            job = self.fetch(source, role_page(source) + schema(source, datePosted=stamp))[0]
            self.assertEqual(job.posted, stamp)
            decision = classify_job(job, now=NOW)
            self.assertEqual(decision.status, 'review')
            self.assertTrue(any('future' in reason for reason in decision.reasons))

    def test_offset_dates_choose_oldest_instant_not_lexicographic_string(self):
        source = curated.ROLE_SOURCES[0]
        page = role_page(source) + schema(source, datePosted='2026-10-03T01:00:00+03:00')
        page += schema(source, datePosted='2026-10-02T23:00:00Z')
        self.assertEqual(self.fetch(source, page)[0].posted, '2026-10-03T01:00:00+03:00')

    def test_structured_applicant_country_is_a_recognizable_restriction(self):
        source = curated.ROLE_SOURCES[0]
        page = role_page(source) + schema(source, applicantLocationRequirements={
            '@type': 'Country', 'name': 'United States'})
        job = self.fetch(source, page)[0]
        self.assertIn('Applicants must be based in United States.', job.description)
        decision = classify_job(job, now=NOW)
        self.assertEqual(decision.status, 'rejected')
        self.assertTrue(any('location' in reason.lower() for reason in decision.reasons))

    def test_structured_role_address_overrides_bare_remote_without_inferring_hq(self):
        source = curated.ROLE_SOURCES[0]
        page = role_page(source) + schema(source, jobLocation={'@type': 'Place', 'address': {
            '@type': 'PostalAddress', 'addressCountry': 'US', 'addressRegion': 'California'}})
        job = self.fetch(source, page)[0]
        self.assertIn('US', job.location)
        self.assertIn('California', job.location)
        self.assertEqual(job.employer_country, '')
        self.assertEqual(classify_job(job, now=NOW).status, 'rejected')
        unfamiliar = self.fetch(source, role_page(source) + schema(source, jobLocation={
            'address': {'addressLocality': 'Unlistedville'}}))[0]
        self.assertEqual(classify_job(unfamiliar, now=NOW).status, 'review')

    def test_explicit_structured_office_and_hybrid_override_visible_remote(self):
        source = curated.ROLE_SOURCES[0]
        for value in ('ON_SITE', 'OnSite', 'on-site', 'IN_OFFICE', 'HYBRID', ['TELECOMMUTE', 'ON_SITE']):
            job = self.fetch(source, role_page(source) + schema(source, jobLocationType=value))[0]
            self.assertEqual(classify_job(job, now=NOW).status, 'rejected')

    def test_structured_global_placeholder_remains_neutral(self):
        source = curated.ROLE_SOURCES[0]
        for value in ('Earth', 'Global', 'Worldwide'):
            job = self.fetch(source, role_page(source) + schema(source, jobLocation={
                'address': {'addressCountry': value}}, jobLocationType='TELECOMMUTE'))[0]
            self.assertEqual(job.location, 'Remote')
            self.assertIn(value, job.description)
            self.assertNotEqual(classify_job(job, now=NOW).status, 'rejected')

    def test_foreign_or_unidentified_jsonld_never_supplies_date_or_restrictions(self):
        source = curated.ROLE_SOURCES[0]
        for changes in ({'title': 'Other Job'}, {'hiringOrganization': {'name': 'Other Company'}},
                        {'hiringOrganization': None}, {'url': 'https://evil.test/job'}):
            page = role_page(source) + schema(source, datePosted='2026-10-19',
                                              description='Foreign requirements', **changes)
            job = self.fetch(source, page)[0]
            self.assertEqual(job.posted, '')
            self.assertNotIn('Foreign requirements', job.description)

    def test_jsonld_restrictions_and_full_description_are_preserved(self):
        source = curated.ROLE_SOURCES[0]
        page = role_page(source) + schema(source,
            description='<p>Additional complete description. Must reside in Canada.</p>',
            applicantLocationRequirements={'@type': 'Country', 'name': 'United States'},
            jobLocation={'address': {'addressRegion': 'California'}},
            eligibilityToWorkRequirement='US work authorization required')
        job = self.fetch(source, page)[0]
        for text in ('Must reside in Canada', 'United States', 'California', 'US work authorization'):
            self.assertIn(text, job.description)

    def test_expired_or_closed_matching_schema_overrides_active_form(self):
        source = curated.ROLE_SOURCES[0]
        for changes in ({'validThrough': '2026-10-01'}, {'description': 'This role has been filled.'}):
            page = role_page(source) + schema(source, **changes)
            self.assertEqual(curated._parse_role(page, source, now=NOW), [])

    def test_duplicate_matching_schema_keeps_oldest_original_date(self):
        source = curated.ROLE_SOURCES[0]
        page = role_page(source) + schema(source, datePosted='2026-10-19') + schema(source, datePosted='2026-08-01')
        self.assertEqual(self.fetch(source, page)[0].posted, '2026-08-01')

    def test_group_three_short_listing_does_not_borrow_other_roles_full_body(self):
        source = curated.ROLE_SOURCES[3]
        self.assertEqual(self.fetch(source, index_page(target_body=False)), [])

    def test_group_three_full_section_isolated_from_unrelated_roles_and_footer(self):
        source = curated.ROLE_SOURCES[3]
        job = self.fetch(source, index_page(other='United States only. This role is closed.'))[0]
        self.assertEqual(job.location, 'Remote Full-time Growth')
        self.assertNotIn('United States', job.description)
        self.assertNotIn('Community Lead', job.description)
        self.assertNotIn('Generic applicant information', job.description)
        self.assertIn('Senior marketing experience', job.description)

    def test_group_three_target_restrictions_and_target_closure_survive(self):
        source = curated.ROLE_SOURCES[3]
        job = self.fetch(source, index_page(extra='<p>Must reside in United States.</p>'))[0]
        self.assertIn('Must reside in United States', job.description)
        self.assertEqual(self.fetch(source, index_page(target_closed='<p>This role is closed.</p>')), [])

    def test_group_three_no_target_application_does_not_borrow_next_role_apply(self):
        source = curated.ROLE_SOURCES[3]
        page = index_page().replace('<a href="mailto:careers@thegroupthree.com">Apply →</a>', '', 1)
        self.assertEqual(self.fetch(source, page), [])


TYB_INDEX = f'''<html><body><h2>{curated.TYB_TITLE}</h2><div>tyb</div>
<div>Marketing &amp; Communications</div><div>Remote</div>
<div>Posted<!-- --> on Sep 29, 2026</div>
<a href="{curated.TYB_APPLY_URL}?utm_source=SoGal">Apply now</a>
<p>At TYB ("Try Your Best"), we are developing a community rewards platform.</p>
</body></html>'''
TYB_BODY_SOURCE = curated.RoleSource(curated.TYB_COSIGN_URL, 'TYB', curated.TYB_TITLE, (), False)


def tyb_body(**changes):
    posting = dict(datePosted='2026-10-18T06:04:22Z', description=(
        'About TYB\nTYB (Try Your Best).\nAbout the Role\n' + TEXT + '\nAbout You\n'
        'Own activation, referrals, retention and lifecycle measurement. '
        'Five years of marketing experience and two years of CRM experience.'))
    posting.update(changes)
    return schema(TYB_BODY_SOURCE, **posting)


class TYBRoleTests(unittest.TestCase):
    def parse(self, index=TYB_INDEX, body=None):
        return curated._parse_tyb(index, body or tyb_body(), now=NOW)

    def test_pair_current_listing_and_matching_full_structured_body(self):
        job = self.parse()[0]
        self.assertEqual(job.company, 'TYB')
        self.assertEqual(job.title, curated.TYB_TITLE)
        self.assertEqual(job.location, 'Remote')
        self.assertEqual(job.posted, '')
        self.assertEqual(job.published_at_upper_bound, '2026-09-29')
        self.assertTrue(job.current_open_verified)
        self.assertIn('activation, referrals, retention', job.description)
        self.assertEqual(job.url, curated.TYB_SOGAL_URL)

    def test_two_exact_bounded_public_requests_and_no_notion_request(self):
        responses = [httpx.Response(200, text=page, request=httpx.Request('GET', url))
                     for page, url in ((TYB_INDEX, curated.TYB_SOGAL_URL),
                                       (tyb_body(), curated.TYB_COSIGN_URL))]
        with patch('boards._get', side_effect=responses) as get:
            self.assertEqual(len(curated.fetch_tyb()), 1)
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         [curated.TYB_SOGAL_URL, curated.TYB_COSIGN_URL])
        for call in get.call_args_list:
            self.assertEqual(call.kwargs, {'follow_redirects': True, 'timeout': 12})

    def test_metadata_and_structured_restrictions_preserved(self):
        index = TYB_INDEX.replace('<div>Remote</div>', '<div>Remote</div><div>Canada only</div>')
        body = tyb_body(applicantLocationRequirements={'name': 'United States'})
        job = self.parse(index, body)[0]
        self.assertEqual(job.location, 'Remote; Canada only')
        self.assertIn('Canada only', job.description)
        self.assertIn('United States', job.description)
        self.assertNotIn('Remote', self.parse(TYB_INDEX.replace('Remote', 'London'))[0].location)

    def test_wrong_missing_or_closed_primary_listing_rejected(self):
        pages = [TYB_INDEX.replace(curated.TYB_TITLE, 'Community Manager'),
                 TYB_INDEX.replace('Try Your Best', 'Other Company'),
                 TYB_INDEX.replace(curated.TYB_APPLY_URL, 'https://evil.test/jobs'),
                 TYB_INDEX.replace('<a href=', '<a hidden href='),
                 TYB_INDEX.replace('</body>', '<p>This position is closed.</p></body>'),
                 '<html><div id="root"></div></html>']
        for page in pages:
            self.assertEqual(self.parse(page), [])

    def test_missing_thin_wrong_identity_or_closed_body_rejected(self):
        bodies = ['<html><div id="root"></div></html>', tyb_body(description='About TYB only'),
                  tyb_body(title='Head of Partner Marketing'),
                  tyb_body(hiringOrganization={'name': 'Different Company'}),
                  tyb_body(validThrough='2026-01-01'),
                  tyb_body(qualifications='This role has been filled.')]
        for body in bodies:
            self.assertEqual(self.parse(body=body), [])

    def test_visible_date_and_mirror_repost_cannot_freshen_old_role(self):
        for stamp, expected in (('Oct 19, 2026', '2026-09-29'),
                                ('Aug 01, 2026', '2026-08-01'),
                                ('invalid', '2026-09-29')):
            job = self.parse(TYB_INDEX.replace('Sep 29, 2026', stamp),
                             tyb_body(datePosted='2026-10-19'))[0]
            self.assertEqual(job.posted, '')
            self.assertEqual(job.published_at_upper_bound, expected)

    def test_older_metadata_from_either_tyb_listing_remains_exclusion_evidence(self):
        index_source = curated.RoleSource(curated.TYB_SOGAL_URL, 'TYB', curated.TYB_TITLE, (), False)
        for index, body in ((TYB_INDEX, tyb_body(datePosted='2026-08-01T11:00:00Z')),
                            (TYB_INDEX + schema(index_source, datePosted='2026-08-01T11:00:00Z'), tyb_body())):
            job = self.parse(index, body)[0]
            self.assertEqual(job.posted, '')
            self.assertEqual(job.published_at_upper_bound, '2026-08-01T11:00:00Z')
            decision = classify_job(job, now=NOW)
            self.assertEqual(decision.status, 'rejected')
            self.assertTrue(any('older than' in reason for reason in decision.reasons))

    def test_getro_earth_placeholder_does_not_invent_a_residence_restriction(self):
        index_source = curated.RoleSource(curated.TYB_SOGAL_URL, 'TYB', curated.TYB_TITLE, (), False)
        index = TYB_INDEX + schema(index_source, applicantLocationRequirements={'@type': 'Country', 'name': 'Earth'})
        job = self.parse(index)[0]
        self.assertIn('Applicant location scope (source): Earth.', job.description)
        self.assertNotIn('must be based in Earth', job.description)
        self.assertNotEqual(classify_job(job, now=NOW).status, 'rejected')

    def test_tyb_contradictory_location_from_either_schema_cannot_qualify(self):
        index_source = curated.RoleSource(curated.TYB_SOGAL_URL, 'TYB', curated.TYB_TITLE, (), False)
        for changes in ({'jobLocation': {'address': {'addressCountry': 'US', 'addressRegion': 'California'}}},
                        {'jobLocationType': 'ON_SITE'}, {'jobLocationType': 'HYBRID'}):
            for index, body in ((TYB_INDEX, tyb_body(**changes)),
                                (TYB_INDEX + schema(index_source, **changes), tyb_body())):
                job = self.parse(index, body)[0]
                self.assertEqual(classify_job(job, now=NOW).status, 'rejected')

    def test_wrong_host_redirect_fails_before_using_job_payload(self):
        response = httpx.Response(200, text=TYB_INDEX, request=httpx.Request('GET', 'https://evil.test/'))
        with patch('boards._get', return_value=response) as get:
            self.assertEqual(curated.fetch_tyb(), [])
        self.assertEqual(get.call_count, 1)


def getblock_page(*, location='Remote', extra='', apply_url=curated.GETBLOCK_APPLY_URL):
    source = curated.ROLE_SOURCES[-1]
    metadata = (f'<div class="mb-8"><ul><li>Vendor / Contractor</li><li>Full time</li>'
                f'<li>{location}</li><li>Marketing</li></ul></div>')
    body = ''.join(f'<h2>{section}</h2><p>{TEXT}</p>' for section in source.sections)
    return (f'<html><body><nav>Jobs</nav><section><h1>{source.title}</h1>{metadata}'
            f'<div class="styledText">{body}<p>3+ years in product marketing, including B2B.</p>'
            '<p>Most of the team works around UTC+3. We need solid overlap with those hours.</p>'
            '<p>Honest remote. GetBlock is fully remote by design, with no US-only, EU-only, '
            'or UK-only restrictions and no hybrid schedule that puts you in an office three days a week.</p>'
            f'{extra}</div>{metadata}<a href="{apply_url}">Apply now</a></section>'
            '<aside>Factorial cookie settings. Unrelated footer message.</aside></body></html>')


class GetBlockRoleTests(unittest.TestCase):
    def setUp(self):
        self.source = next(source for source in curated.ROLE_SOURCES if source.url == curated.GETBLOCK_URL)

    def fetch(self, page=None, *, url=None, status=200):
        with patch('boards._get', return_value=response(page or getblock_page(), self.source,
                                                        url=url, status=status)):
            return curated._fetch_one(self.source)

    def test_exact_new_requisition_full_body_and_live_cta_without_visiting_form(self):
        with patch('boards._get', return_value=response(getblock_page(), self.source)) as get:
            job = curated._fetch_one(self.source)[0]
        get.assert_called_once_with(curated.GETBLOCK_URL, follow_redirects=True, timeout=12)
        self.assertEqual(job.title, 'Full-Stack Marketing Manager / Marketing PMM')
        self.assertEqual(job.company, 'GetBlock')
        self.assertEqual(job.url, curated.GETBLOCK_URL)
        self.assertEqual(job.source, 'company/curated')
        self.assertEqual(job.location, 'Remote')
        self.assertEqual(job.posted, '')
        self.assertEqual(job.published_at_upper_bound, curated.KNOWN_ROLE_EXISTENCE.get(job.url, ''))
        self.assertTrue(job.current_open_verified)
        self.assertIn('3+ years in product marketing, including B2B', job.description)
        self.assertNotIn('cookie settings', job.description)
        self.assertNotIn('Unrelated footer', job.description)

    def test_preserves_timezone_overlap_and_negated_geographic_office_restrictions(self):
        job = self.fetch()[0]
        self.assertIn('UTC+3', job.description)
        self.assertIn('solid overlap', job.description)
        self.assertIn('no US-only, EU-only, or UK-only restrictions', job.description)
        self.assertIn('no hybrid schedule', job.description)
        self.assertEqual(job.location, 'Remote')

    def test_header_geography_and_body_additional_restrictions_are_preserved(self):
        job = self.fetch(getblock_page(location='Remote · Canada',
                                     extra='<p>Applicants must reside in France.</p>'))[0]
        self.assertEqual(job.location, 'Remote · Canada')
        self.assertIn('Applicants must reside in France', job.description)
        self.assertEqual(classify_job(job, now=NOW).status, 'rejected')

    def test_missing_remote_header_is_not_filled_from_company_prose(self):
        self.assertEqual(self.fetch(getblock_page(location=''))[0].location, '')

    def test_old_298460_is_distinct_and_cannot_substitute_as_apply_target(self):
        job = self.fetch()[0]
        old_url = curated.GETBLOCK_URL.replace('327722', '298460')
        self.assertNotEqual(job.id, boards._make_id(old_url))
        self.assertEqual(self.fetch(url=old_url), [])
        self.assertEqual(self.fetch(getblock_page(apply_url=curated.GETBLOCK_APPLY_URL.replace('327722', '298460'))), [])

    def test_generic_wrong_host_disabled_or_missing_apply_fails_closed(self):
        for target in ('https://evil.test/apply/full-stack-marketing-manager-marketing-pmm-327722',
                       '/apply', '#', 'mailto:careers@getblock.io'):
            self.assertEqual(self.fetch(getblock_page(apply_url=target)), [])
        for attribute in ('hidden', 'aria-disabled="true"', 'style="display:none"'):
            self.assertEqual(self.fetch(getblock_page().replace('<a href=', f'<a {attribute} href=')), [])
        self.assertEqual(self.fetch(getblock_page().replace('Apply now', 'Subscribe')), [])

    def test_explicit_closure_inside_or_outside_role_overrides_apply(self):
        for page in (getblock_page(extra='<p>This role is closed.</p>'),
                     getblock_page().replace('<body>', '<body><p>Applications are closed.</p>')):
            self.assertEqual(self.fetch(page), [])

    def test_redirect_http_error_generic_200_and_truncated_bodies_fail_closed(self):
        for status in (403, 404, 410, 429, 500):
            self.assertEqual(self.fetch(status=status), [])
        for url in ('https://getblock.factorial.com/', curated.GETBLOCK_APPLY_URL,
                    curated.GETBLOCK_URL.replace('factorial.com', 'factorial.com.evil.test')):
            self.assertEqual(self.fetch(url=url), [])
        for page in ('Access denied', '<html><div id="root"></div></html>',
                     getblock_page().replace('styledText', 'empty'),
                     getblock_page().replace("What you'll bring", 'Missing requirements')):
            self.assertEqual(self.fetch(page), [])

    def test_unstructured_mirror_date_is_never_assumed_to_be_original(self):
        job = self.fetch(getblock_page(extra='<p>External mirror posted October 14, 2026.</p>'))[0]
        self.assertEqual(job.posted, '')
        self.assertEqual(job.published_at_upper_bound, curated.KNOWN_ROLE_EXISTENCE.get(job.url, ''))


if __name__ == '__main__':
    unittest.main()

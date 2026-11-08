# Free source expansion

Reviewed on 2026-11-08. These changes collect public job evidence only. They do not submit applications, create accounts, use credentials, add dependencies or paid APIs, change delivery, or change schedules.

## Employer discovery with a review gate

`career_registry.json` contains 194 discovery records derived from [crypto-jobs-fyi/crawler](https://github.com/crypto-jobs-fyi/crawler/blob/de065756718f18ff29bda45438ea74129c6e5bf5/crypto_companies.json). The exact source commit and blob are recorded in its provenance. The Apache-2.0 license and modification notice are included under `third_party/crypto-jobs-fyi`. No upstream scraper code or scraped job dataset is installed or executed.

The registry is not a list of 194 verified employers or active sources. A row is fetched only after its exact ATS URL is independently associated with the company's official site. The first reviewed additions are Kraken, Blockstream, Phantom, Risk Labs, CoinTracker, Rain, Blockworks, Tenderly and Plasma. Their official evidence URLs and review dates are recorded beside each row. A careers redirect or public embedded API configuration can establish the association.

Existing curated sources take precedence. Unreviewed and unsupported URLs cause no network requests. Reviews older than 90 days or dated in the future fail closed. Changing a row's careers URL invalidates its review. URLs are limited to supported HTTPS ATS hosts and validated board slugs; no arbitrary upstream configuration is executed. At most 12 additional reviewed boards run in one collection. Overflow is explicitly reported as bounded coverage.

Run `python career_registry.py --audit` for a network-free audit. The public collection manifest also includes this audit. At the review date, the 194 rows comprised 9 added boards, 16 already-covered boards, 137 associations awaiting review and 32 unsupported/non-board URLs. Counts are discovery coverage, not job counts or proof that no other jobs exist.

To extend coverage, verify a company's official association and exact public endpoint, review its access/data terms, add a dated review record, run the offline tests and bounded live check, and review the resulting diff. Do not auto-approve refreshed upstream data or infer headquarters from the registry.

## Workable and Teamtailor

- Workable uses the [documented public GET endpoint](https://workable.readme.io/reference/jobs-1), `https://www.workable.com/api/accounts/{subdomain}?details=true`. The initial reviewed board is Reown (`walletconnect`), linked from [Reown careers](https://reown.com/careers). It needs no key. Repeated shortcodes are merged without discarding any location or description constraints.
- Teamtailor uses its [documented public RSS feed](https://support.teamtailor.com/en/articles/11171756-rss-feed-how-to-guide), not its authenticated employer API or an undocumented widget. The initial board is [Crossmint](https://www.crossmint.com/careers). It checks up to three 100-entry pages; repeated pages, ignored sizes and caps are reported. Structured locations and remote/hybrid/on-site status remain available to the filters.

Both adapters share the existing byte, timeout and concurrency limits. Failures and malformed rows are not reported as successful empty searches. No retries attempt to bypass access blocks or rate limits. Explicit closed duplicates win. Dates with uncertain first-publication semantics are exclusion-only upper bounds, never invented original posting dates. Current data comes from live responses rather than cached job descriptions. Employer-base verification and all existing age, geography, title and experience checks still apply.

Workable tenant/global URL variations and Crossmint title-slug changes retain the same exact requisition identity. Recognized query selectors still distinguish jobs. Existing history keys remain intact; read-only aliases preserve earliest age and expiry tombstones. This does not change delivery ledgers or reset state.

## Deliberately excluded

Himalayas is not fetched or registered in this public collector. Its [API terms](https://himalayas.app/terms) require an unchanged application link, visible attribution, expired-job removal and no onward distribution through feeds. Public GitHub data exports are not a suitable default integration for those rows. An independently designed private integration would be a separate change; no inactive adapter here can accidentally leak data through deduplication.

The larger Ever Jobs framework is not installed. Only supported public ATS interfaces inform the new independently written adapters. Paid X API collection, authenticated scraping, browser-cookie extraction and application automation are not part of this upgrade. Source-code licenses do not by themselves authorize republishing third-party job descriptions.

## Validation

Run `python -m unittest discover -s tests -v` and `python -m compileall -q .`. Tests are offline and cover registry gates, caps, exact-identity normalization, geographic preservation, malformed feeds, closed duplicates, old-date history and unchanged legacy database bytes. Live source checks are separate from the test suite. Zero jobs returned by an otherwise successful feed is recorded as zero current results, not evidence of complete market coverage.

A bounded live check on the review date fetched 206 normalized roles from the new sources (201 registry/Ashby, five Crossmint, zero Reown), with no source failures. Fifteen titles contained marketing/growth/GTM words, including engineering false positives. Existing filters and posting history rejected all 206; broader coverage is not a claim of new qualifying opportunities. Those filters were not relaxed to increase the result count.

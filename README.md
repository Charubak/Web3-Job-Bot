# Daily research collection (29 October 2026)The new no-message workflow collects public job evidence at12:30/12:45IST for a separate research-capable digest. It publishes a compact manifest, readable candidate queue, connector-sized evidence shards and durable posting-date history on branch `research-data`. It does not claim that collection alone completes native X/LinkedIn search or resolves unknown eligibility. See [the input contract, remaining source limits and reversible delivery cutover](docs/research-digest-contract.md).

Legacy Telegram delivery remains available during verification. Setting repository variable `JOB_DIGEST_DELIVERY=chatgpt` disables its workflow without deleting credentials or delivery history; the collection workflow continues independently. Do this only after verifying the replacement schedule and fresh public inputs.

# Web3 Growth / GTM / Product Marketing Job Bot

A conservative job search for experienced growth and product-marketing roles. Preview locally or run a bounded scheduled search in GitHub Actions. An always-on Fly.io server is **not required**.

## What gets an alert

All checks must pass:

- Mid/senior Growth, GTM, Product Marketing or relevant Web3 content-marketing scope matching an experienced individual contributor, specialist, manager or player-coach. A lead/head title is not required. Early-stage Head of Growth roles need appropriate hands-on scope.
- A genuine remote role with a usable full description from the employer, its ATS, or an independently verified investor/ecosystem hiring page. Bare “Remote” can qualify when no geographic restriction is stated; this is not a guarantee of India eligibility. A distributed company alone does not establish that the role is remote.
- No required city/country/region of residence, relocation, office/hybrid attendance, or conflicting eligibility restriction. Time-zone overlap is recorded separately for review.
- Employer has recently verified headquarters/base outside India. An Indian office or a job's location does not establish the employer's headquarters.
- No minimum posting age. Known dates from now through 30 days ago qualify; known older listings are excluded permanently for the same identity. A currently open, verified role can qualify when its original date is unknown, without inventing a date. Future dates stay in review. Dated alerts are selected newest first, followed by undated openings.

Business development, community, events-focused, junior/intern, technical engineering and sales roles are excluded. Experienced Web3 content-marketing and storytelling roles can qualify when their marketing scope is clear; unrelated writing or technical documentation does not. A relevant marketing role mentioning incidental events is not automatically an events role.

Missing role content, employer-base evidence or unresolved restrictions go to **review**. Unknown original age alone does not block a verified current opening, and a genuine Remote role needs no affirmative worldwide claim. Expect fewer results, including zero matches on some runs. The conservative rules are auditable heuristics, not a substitute for reading the employer's current posting.

## Source coverage and limits

Alongside the existing Greenhouse, Lever, public job boards and public-channel sources, the bot now checks seven officially linked Ashby boards: Trust Wallet, Allium, Keyrock, Ether.fi, Lido, Injective and Morpho. The company-to-board evidence links are in `direct_sources.py`. Jito’s verified Lever board is also checked directly so its North America restriction is retained instead of relying on a vague aggregator label. Direct Gains careers checks require the current index listing, exact role page and active application form; they do not submit applications. A narrowly scoped Hacken page adapter also retains verified older-listing evidence, so that known-old role is excluded rather than resurfaced.

Ashby `publishedAt` means **last publication**, not original posting. It is stored only as an existence-date upper bound. A last-publication date over 30 days old proves exclusion and persists the same-identity expiry; a recent republication cannot establish an original date or reset old history. Original dates remain unknown unless separately supported.

X/Twitter discovery uses four bounded, role-specific public search-index queries with a past-month hint. Two additional LinkedIn queries target actual public posts/feed updates from hiring teams, rather than LinkedIn Jobs pages. Search snippets never establish employer identity, location, headquarters or original posting date. Blocked/CAPTCHA responses stop the source; there is no login, paid API, scraping bypass or promise of complete X coverage. Newly found first-party links still need verification. Neither index guarantees access to complete profiles, pinned posts or every founder announcement. Employer links discovered during research can be added only after verification; snippets are never promoted to eligibility evidence. No scraper can guarantee that every internet opening is covered.

Requested one-time recommendations use independently researched current roles and concise fit lines. Routine alerts use the same newest-first, remote and role preferences, while retaining their evidence and uncertainty in audit reports. Known-old, closed or explicitly restricted jobs are not presented as candidate matches.

The daily loop also rechecks exact verified company or investor listings for Defuse Labs, PancakeSwap, Intercepta, TYB and GetBlock, plus Hylo’s Solana ecosystem listing. These bounded adapters require live role content and application evidence; changed, closed or incomplete pages are omitted rather than reused from cached text. The Group Three’s thin careers cards can be manually researched, but the automatic full-description adapter omits them. PancakeSwap’s precise employer base is not promoted to verified from a job-location label.

Routine Telegram messages contain a title, company, short fit line and application link. Salary estimates and lengthy verification notes are omitted. Source-level failures remain visible in logs and audit reports.

## Employer evidence coverage

`employers.json` stores exact company aliases, official source URLs, source excerpts, and verification dates. Current coverage: 17 verified non-India employers and 9 explicitly unknown employers, checked 19 October 2026. Other employers remain unknown. Filters require evidence no older than 90 days; refresh the cited source and verification date after actually rechecking it. Do not update a date without verification, infer HQ from a role location, or extend a parent-company record to an unidentified subsidiary.

`filter_jobs()` returns qualified jobs plus review/rejected decisions with reasons. `apply_filters()` is the compatibility wrapper returning qualified jobs only. A JSON report includes reasons for all decisions; no credentials or full descriptions are included.

## Local preview and tests

Python 3.11 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scraper.py --dry-run --report run_report.json
```

`python scraper.py` also defaults to preview. Preview does not require Telegram credentials, send messages, create a deduplication database, modify an existing database, or mark jobs as seen. `--report` explicitly writes only the requested report.

Board requests are bounded and run with limited concurrency. Source errors are logged; if every source returns no jobs, the run fails rather than reporting success. Some job boards may block automated requests or change format. Offline tests mock network and Telegram calls.

## Optional scheduled GitHub Actions operation

The repository includes:

- `tests.yml`: offline tests on pull requests and relevant pushes. No Telegram secrets are provided.
- `setup.yml`: deployment readiness on a `main` push that changes this setup workflow, or manual dispatch. Reports only whether the two existing Telegram secrets are present, never their values. It confirms the public cache inventory, restores the exact newest state for preview or initializes only a confirmed missing baseline, verifies the new cache, and never sends messages. Inventory/restore errors stop initialization. Existing seen/pending history is not reset.
- `activate.yml`: one owner-authorized activation confirmation to the configured private chat after verifying the exact bot. Its durable acknowledgment enables delivery. A pending/uncertain confirmation blocks reactivation and alerts until reviewed.
- `latest-job-review.yml`: one requested updated review after updating the age and remote rules and expanding direct discovery; separately fenced and unable to replay earlier status messages.
- `job-review-status.yml`: one owner-requested, separately fenced job review message after its first merge; no recurring review messages or extra search schedule.
- `review-current.yml`: one no-message review after its initial merge (or explicit manual dispatch), refreshing public posting-age/expiry history without changing delivery or activation records.
- `jobs.yml`: targets 07:30 UTC (13:00 India Standard Time), with hourly fallback checks and one status per India date. Alerts stay disabled until an activation acknowledgment exists for the current bot token and private recipient. Set repository variable `JOB_ALERTS_ENABLED=false` to stop scheduled checks. The first successful activation also triggers one immediate search. Manual mode defaults to `preview`.

The latest requested recommendation also fences its public listing identities and exact shared-page role titles before sending, so the normal daily loop cannot blindly repeat a confirmed or uncertain manual delivery.

Already-sent one-shot message payloads retain their original text for receipt integrity; they are historical records, not the active age policy above.

The scheduled workflow uses standard `ubuntu-latest` runners, 12-minute timeouts, read-only repository permissions, pinned actions, and no paid AI APIs. Both workflows refuse automatic execution in a private repository; the job-search workflow also refuses forks and non-default branches.

GitHub currently provides standard hosted-runner usage free for public repositories. Private-repository minutes, artifact/cache storage, larger runners and account settings have separate limits/billing. This is not a promise of unlimited free service. No paid services or subscriptions are enabled by this code. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions). The workflow uses no artifact uploads and stores only a small SQLite delivery-state cache.

### Activation checklist (explicit owner actions)

1. Review and merge after tests pass. The first merge adding the activation workflow performs the explicitly authorized activation; later changes do not automatically repeat it. Do not merge that activation until the owner has confirmed the intended private-chat ID.
2. Inspect the successful Deployment readiness run, including source errors, review reasons and employer-evidence coverage. If no readiness run completed, manually run Scheduled job search in `preview` mode.
3. If Deployment readiness did not initialize a baseline, run `initialize`. This records currently qualified matches as a baseline **without sending anything**. It does not mark review jobs as seen. Future newly qualified matches can still be delivered. Do not reset a restored delivery history.
4. Keep `TELEGRAM_BOT_TOKEN` in repository Actions secrets. Supply the verified private recipient either with the optional `TELEGRAM_CHAT_ID` secret or the authenticated encrypted `recipient.json` configuration. That envelope uses a purpose-specific public encryption key; only the existing bot token can derive the runtime decryption key. Never put the raw recipient or token in public source, issues or logs. If both recipient settings exist they must match; conflicting configuration fails closed. Verify the original private recipient before activation.
5. Run **Activate verified private-chat job alerts** (first approved merge or explicit manual dispatch). It verifies `@charubak_web3jobs_bot` and the configured private chat, persists a pending fence, sends one confirmation, and records the acknowledgment. Only that acknowledgment permits later job alerts. No activation-message retries occur automatically. Set `JOB_ALERTS_ENABLED=false` to stop scheduled checks; unset it or set `true` to allow checks again.
6. If credentials or recipient change, activation becomes invalid. Changing the bot token also requires resealing the encrypted recipient (or removing the stale envelope and supplying the explicit chat-ID secret). Reactivate explicitly after verifying the new configuration. Stop an older deployment separately after the new delivery path is verified; changing this repository does not replace a running Fly image.

The initial baseline deliberately sends no existing listings. To inspect them, use preview. To receive those existing listings too, use an explicitly initialized empty local state after reviewing them, rather than deleting the production deduplication cache blindly.

### State and delivery safety

Posting history retains the earliest observed posting date and permanent expiry tombstones for canonical listing identities. Listings older than 30 days stay suppressed even if the same URL is later reposted with a newer date. Tracking parameters and fragments do not create a new job identity. History refreshes during normal scheduled runs and explicit `--refresh-history`; ordinary preview remains read-only. Tombstones are retained, not deleted after a month.

Only acknowledged Telegram chunks are marked sent. A later message failure preserves earlier acknowledged chunks. Ambiguous network/server failures quarantine the affected job IDs in a separate `unconfirmed_jobs` table; they are not marked delivered and future automatic runs skip them. Delivery may have occurred before a timeout. Inspect the chat before explicitly replaying those jobs; a successful explicit `/jobs` replay acknowledges them and clears their quarantine.

Actions restores `data/seen_jobs.db` from its cache. Before any message delivery, it prepares a fresh delivery plan, fences selected IDs as unconfirmed, saves that database under a unique `-prepared` cache key, and verifies the exact cache exists. A cache-save warning alone is insufficient to permit delivery. After delivery it writes a newer `-final` cache, even when a later chunk fails. Restore selects the most recently created cache with the common prefix: normally final state; if cancellation/final-save failure intervened, the prepared fence keeps those jobs from being automatically resent. Some unsent jobs may consequently need manual reconciliation, which is safer than duplicate alerts.

Caches can expire or be evicted and are not a permanent database. If all state is missing, sending fails closed: preview and explicitly initialize a new baseline before resuming. This avoids a missing-state flood. Retaining an older cache while losing a newer one can still lose delivery history; review cache failures and reconcile before retrying affected runs. Local/always-on mode writes its fence to SQLite before each request and needs persistent storage.

Do not put credentials or unencrypted private data in cached files. GitHub Actions caches in a public repository can be readable by others. The database contains public listing IDs/titles/URLs, earliest posting dates, expiry tombstones and seen timestamps, plus activation state bound to an opaque HMAC of the recipient using the existing bot token. An optional historical recipient-recovery step can add only Fernet-authenticated ciphertext under a key derived from the existing token. The database never contains the token, raw chat ID, profile information or message contents.

GitHub schedules can be delayed or dropped under load, run only on the default branch, and can be disabled after 60 days of public-repository inactivity. Check Actions periodically and re-enable if required. Scheduled execution sends alerts; it does **not** run a continuously listening Telegram command bot. See [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Optional existing local/always-on bot

`bot.py` remains available for `/jobs`, `/new`, `/twitter`, `/stats`, `/clear` and `/help` when you explicitly run it in an existing environment. It uses the same strict filters and employer evidence. Set credentials in an untracked `.env` and start `python bot.py` only when delivery to the configured chat is intended. On startup it runs a scrape and then polls Telegram.

`fly.toml` is retained for existing installations; this patch neither starts nor cancels a Fly service. Fly.io should not be assumed free. Migrating the search does not stop existing Fly machines/volumes or charges. Review the existing Fly account separately before making authorized changes: [Fly pricing](https://fly.io/docs/about/pricing/).

## Daily afternoon status (October 2026)

The daily workflow now sends a status even when there are no fresh verified
matches. It distinguishes source failures, zero returned listings, withheld
review candidates and previously delivered jobs. It includes still-open qualifying jobs as explicitly labeled reminders,
with new roles distinguished from previously shared roles. Known posting dates
must remain within 30 days; active unknown-date roles appear in a separate
section and are never claimed to fall within that window. Closed, expired and
currently unverifiable openings are withheld. Every reminder comes from fresh
source results, not cached role contents. Unknown original posting dates are labeled on job cards.

The existing SQLite/cache history is preserved. A separate India-date ledger
fences status delivery before Telegram and records its acknowledgment. An
ambiguous Telegram outcome stops automatic retries pending reconciliation.
Missing state fails closed: do not initialize or delete caches to fix delivery.
The `mode=test` uses an independent test key and does not mark jobs sent,
pending or expired. An explicitly requested additional preview can supply a
unique `test_id`; reusing it cannot send twice. Preview and initialization remain explicitly separate.

The target is **13:00 Asia/Kolkata (07:30 UTC)**. GitHub cron can run late or drop
runs, so `30 7-12 * * *` provides hourly recovery checks with a daily send guard;
it is not an exact-time guarantee. Standard GitHub runners for this public repo
are free. All deliveries share the existing concurrency group.

For the authorized Mac, `scripts/morning_dispatch.py` uses the existing `gh`
login to dispatch `jobs.yml --ref main -f mode=send` after 13:00 IST, once per
India date, starting October 23. A launchd agent invokes it every minute; it
catches up after wake/network recovery. It needs the Mac logged in, online and
awake at 13:00 for timely dispatch; no new credentials or paid service is used.
GitHub's independent cron remains fallback. Even if two triggers arrive, the
shared workflow ledger prevents a second daily status. Queue/startup/scrape time
still adds latency. `--check` is read-only and does not dispatch.

Verification: run `python -m unittest discover -s tests -v`. For a real daily
check use `gh workflow run jobs.yml --repo Charubak/Web3-Job-Bot --ref main -f
mode=send`; inspect acknowledgment and final-cache verification, not merely a
green workflow. Do not automatically retry a pending/unconfirmed digest.

The daily digest includes all qualifying active roles, split across Telegram-safe
messages if necessary. Public Telegram discovery also checks @web3marketingjobs
and @web3_marketing_jobs; source failures remain explicit and do not trigger any
access-control bypass.

### Readable digest and reviewed public roles

Daily cards use pins, NEW/REMINDER labels, location, original posting date where
known, published salary where supplied, and one Apply link. No salary estimates
or generated responsibility blurbs are used. Dedicated Social Media Manager and
BD titles are excluded; broad marketing roles may include social and partnership
work. Narrow PMM roles rank below broad marketing/growth within their date groups.

The Binance Greenhouse board supplements its older Lever feed. An exact public
Zoniqx LinkedIn posting is re-fetched without login/cookies, not a general or
authenticated daily LinkedIn search. Two individually reviewed roles are bounded
to their original October dates and require live source checks. The Zoniqx
independent marketing-owner role is a deliberate reviewed 2+ year exception;
Binance is labeled adjacent and retains its remote/team and employing-entity
caveats, rather than being declared automatically qualified. Reviewed inclusion
expires by November 20 and respects older stored posting history. Source access
failures are reported and never replaced with cached descriptions.

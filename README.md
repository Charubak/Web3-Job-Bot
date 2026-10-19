# Web3 Growth / GTM / Product Marketing Job Bot

A conservative job search for experienced growth and product-marketing roles. Preview locally or run a bounded scheduled search in GitHub Actions. An always-on Fly.io server is **not required**.

## What gets an alert

All checks must pass:

- Senior Growth, GTM, or Product Marketing scope matching an experienced individual contributor or player-coach. Early-stage Head of Growth roles need appropriate hands-on scope.
- Explicit worldwide/anywhere remote hiring evidence in a first-party employer ATS listing and a usable full description. Bare “Remote,” a distributed company, or a third-party repost alone is insufficient.
- No required city/country/region of residence, relocation, office/hybrid attendance, or conflicting eligibility restriction. Time-zone overlap is recorded separately for review.
- Employer has recently verified headquarters/base outside India. An Indian office or a job's location does not establish the employer's headquarters.
- A verified posting age of 14–30 days inclusive. Unknown or unparseable dates stay in review; younger and older listings do not qualify.

Business development, community, events-focused, junior, technical, sales and content-only roles are excluded. A relevant marketing role mentioning incidental events is not automatically an events role.

Missing or ambiguous evidence goes to **review**, never to a qualified alert. Expect fewer results, including zero matches on some runs. The conservative rules are auditable heuristics, not a substitute for reading the employer's current posting.

## Source coverage and limits

Alongside the existing Greenhouse, Lever, public job boards and public-channel sources, the bot now checks seven officially linked Ashby boards: Trust Wallet, Allium, Keyrock, Ether.fi, Lido, Injective and Morpho. The company-to-board evidence links are in `direct_sources.py`. Jito’s verified Lever board is also checked directly so its North America restriction is retained instead of relying on a vague aggregator label. Direct Gains careers checks require the current index listing, exact role page and active application form; they do not submit applications. A narrowly scoped Hacken page adapter also retains verified older-listing evidence, so that known-old role is excluded rather than resurfaced.

Ashby `publishedAt` means **last publication**, not original posting. It is stored only as an existence-date upper bound. A last-publication date over 30 days old proves exclusion and persists the same-identity expiry; a recent republication cannot establish eligibility or reset old history. Original dates remain unknown unless separately supported.

X/Twitter discovery uses four bounded, role-specific public search-index queries with a past-month hint. Search snippets never establish employer identity, location, headquarters or original posting date. Blocked/CAPTCHA responses stop the source; there is no login, paid API, scraping bypass or promise of complete X coverage. Newly found first-party links still need verification. No scraper can guarantee that every internet opening is covered.

The owner-requested one-time job review is separate from routine qualified alerts. It may label a specifically requested open role as needing verification, with the missing evidence stated. Known-old, closed or explicitly restricted jobs are not presented as candidate matches.

## Employer evidence coverage

`employers.json` stores exact company aliases, official source URLs, source excerpts, and verification dates. Initial coverage: 11 verified non-India employers and 9 explicitly unknown employers, checked 19 October 2026. Other employers remain unknown. Filters require evidence no older than 90 days; refresh the cited source and verification date after actually rechecking it. Do not update a date without verification, infer HQ from a role location, or extend a parent-company record to an unidentified subsidiary.

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
- `job-review-status.yml`: one owner-requested, separately fenced job review message after its first merge; no recurring review messages or extra search schedule.
- `review-current.yml`: one no-message review after its initial merge (or explicit manual dispatch), refreshing public posting-age/expiry history without changing delivery or activation records.
- `jobs.yml`: checks once daily at 03:30 UTC (09:00 India Standard Time). Alerts stay disabled until an activation acknowledgment exists for the current bot token and private recipient. Set repository variable `JOB_ALERTS_ENABLED=false` to stop scheduled checks. The first successful activation also triggers one immediate search. Manual mode defaults to `preview`.

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

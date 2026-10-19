# Web3 Growth / GTM / Product Marketing Job Bot

A conservative job search for experienced growth and product-marketing roles. Preview locally or run a bounded scheduled search in GitHub Actions. An always-on Fly.io server is **not required**.

## What gets an alert

All checks must pass:

- Senior Growth, GTM, or Product Marketing scope matching an experienced individual contributor or player-coach. Early-stage Head of Growth roles need appropriate hands-on scope.
- Explicit worldwide/anywhere remote hiring evidence in a first-party employer ATS listing and a usable full description. Bare “Remote,” a distributed company, or a third-party repost alone is insufficient.
- No required city/country/region of residence, relocation, office/hybrid attendance, or conflicting eligibility restriction. Time-zone overlap is recorded separately for review.
- Employer has recently verified headquarters/base outside India. An Indian office or a job's location does not establish the employer's headquarters.
- A parseable, recent posting date (45 days maximum).

Business development, community, events-focused, junior, technical, sales and content-only roles are excluded. A relevant marketing role mentioning incidental events is not automatically an events role.

Missing or ambiguous evidence goes to **review**, never to a qualified alert. Expect fewer results, including zero matches on some runs. The conservative rules are auditable heuristics, not a substitute for reading the employer's current posting.

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
- `jobs.yml`: search at 00:17, 06:17, 12:17 and 18:17 UTC, **disabled unless** repository variable `JOB_ALERTS_ENABLED` is set to `true`. Manual mode defaults to `preview`.

The scheduled workflow uses standard `ubuntu-latest` runners, 12-minute timeouts, read-only repository permissions, pinned actions, and no paid AI APIs. Both workflows refuse automatic execution in a private repository; the job-search workflow also refuses forks and non-default branches.

GitHub currently provides standard hosted-runner usage free for public repositories. Private-repository minutes, artifact/cache storage, larger runners and account settings have separate limits/billing. This is not a promise of unlimited free service. No paid services or subscriptions are enabled by this code. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions). The workflow uses no artifact uploads and stores only a small SQLite delivery-state cache.

### Activation checklist (explicit owner actions)

1. Review and merge the patch into `main` after tests pass. Merging alone does not enable scheduled delivery.
2. Inspect the successful Deployment readiness run, including source errors, review reasons and employer-evidence coverage. If no readiness run completed, manually run Scheduled job search in `preview` mode.
3. If Deployment readiness did not initialize a baseline, run `initialize`. This records currently qualified matches as a baseline **without sending anything**. It does not mark review jobs as seen. Future newly qualified matches can still be delivered. Do not reset a restored delivery history.
4. In repository Settings → Secrets and variables → Actions, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` yourself for the intended bot and private chat. Verify that recipient before activation. Do not paste credentials into chat, source files, issues or logs.
5. Only when ready to send to that recipient, run manual mode `send`. Then set repository variable `JOB_ALERTS_ENABLED=true` to enable future scheduled delivery. Set it to `false` to stop scheduling.

The initial baseline deliberately sends no existing listings. To inspect them, use preview. To receive those existing listings too, use an explicitly initialized empty local state after reviewing them, rather than deleting the production deduplication cache blindly.

### State and delivery safety

Only acknowledged Telegram chunks are marked sent. A later message failure preserves earlier acknowledged chunks. Ambiguous network/server failures quarantine the affected job IDs in a separate `unconfirmed_jobs` table; they are not marked delivered and future automatic runs skip them. Delivery may have occurred before a timeout. Inspect the chat before explicitly replaying those jobs; a successful explicit `/jobs` replay acknowledges them and clears their quarantine.

Actions restores `data/seen_jobs.db` from its cache. Before any Telegram request, it prepares a fresh delivery plan, fences selected IDs as unconfirmed, saves that database under a unique `-prepared` cache key, and verifies the exact cache exists. A cache-save warning alone is insufficient to permit delivery. After delivery it writes a newer `-final` cache, even when a later chunk fails. Restore selects the most recently created cache with the common prefix: normally final state; if cancellation/final-save failure intervened, the prepared fence keeps those jobs from being automatically resent. Some unsent jobs may consequently need manual reconciliation, which is safer than duplicate alerts.

Caches can expire or be evicted and are not a permanent database. If all state is missing, sending fails closed: preview and explicitly initialize a new baseline before resuming. This avoids a missing-state flood. Retaining an older cache while losing a newer one can still lose delivery history; review cache failures and reconcile before retrying affected runs. Local/always-on mode writes its fence to SQLite before each request and needs persistent storage.

Do not put credentials or private data in cached files. GitHub Actions caches in a public repository can be readable by others. The database contains only public listing IDs/titles/URLs and seen timestamps.

GitHub schedules can be delayed or dropped under load, run only on the default branch, and can be disabled after 60 days of public-repository inactivity. Check Actions periodically and re-enable if required. Scheduled execution sends alerts; it does **not** run a continuously listening Telegram command bot. See [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Optional existing local/always-on bot

`bot.py` remains available for `/jobs`, `/new`, `/twitter`, `/stats`, `/clear` and `/help` when you explicitly run it in an existing environment. It uses the same strict filters and employer evidence. Set credentials in an untracked `.env` and start `python bot.py` only when delivery to the configured chat is intended. On startup it runs a scrape and then polls Telegram.

`fly.toml` is retained for existing installations; this patch neither starts nor cancels a Fly service. Fly.io should not be assumed free. Migrating the search does not stop existing Fly machines/volumes or charges. Review the existing Fly account separately before making authorized changes: [Fly pricing](https://fly.io/docs/about/pricing/).

# Readable Digest Implementation Plan

**Goal:** Put actionable, factual job cards before diagnostics without changing eligibility or delivery state.

**Architecture:** Keep selection and delivery unchanged. `daily_digest.render` supplies a two-line heading; `digest_content` builds numbered cards, groups by newness/date confidence, and appends a brief coverage caveat. Summaries require supporting phrases in current fetched descriptions; fallback quotes a responsibility sentence or points to the description without inventing duties.

**Tech Stack:** Python, unittest, Telegram HTML.

- [x] Inspect the acknowledged production preview and receipt in run 39826345747.
- [x] Replace diagnostic summary in daily_digest.py; retain test label.
- [x] Replace digest_content.py formatting, preserve per-chunk new-job acknowledgment lists and length limit.
- [x] Update tests/test_daily_digest.py and tests/test_reminder_digest.py for plain-language coverage, truthful unknown age, HTML escaping, stale-summary prevention, numbered chunking and acknowledgment membership.
- [ ] Run `python3 -m unittest discover -s tests` and inspect result.
- [ ] Render the four actual previous roles with production receipt counts and original newness labels to data/readable-preview.txt; no network send or state writes.
- [ ] Present exact preview to parent before publishing or any additional send.

Separate uncommitted Binance source work is excluded from this formatting patch. No scheduler, ledger, selection or Telegram transport changes are required.

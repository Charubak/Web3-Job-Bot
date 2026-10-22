# Daily Digest Implementation Plan

> For agentic workers: execute inline using the executing-plans workflow. Publishing and one labeled test are authorized; retain all existing delivery history.

**Goal:** Send a truthful daily status after 13:00 Asia/Kolkata, including empty or partial searches, without repeating delivered roles.

**Architecture:** Keep existing job assessment and delivery fences. Add structured source coverage and a separate daily status ledger in the existing cached database, persist before Telegram, acknowledge after Telegram. Share the workflow concurrency group and daily key between cron fallback and manual dispatch.

**Tech Stack:** Python 3.11, SQLite, unittest, GitHub Actions, existing Telegram configuration.

- [ ] Add source-health records at adapter errors and per-adapter completion; distinguish zero results from unavailable coverage.
- [ ] Permit empty searches only for the daily status path. Include coverage in run reports. Preserve existing preview failure behavior.
- [ ] Add daily status prepare/deliver commands with India-date keys and a fixed one-time test key. Never reset or reinitialize state. Fence status before sending, block ambiguous retries, record acknowledgments.
- [ ] Wire jobs.yml to status preparation, durable cache verification, delivery and final cache verification. Retain safe preview/initialize modes. Add hourly afternoon fallback checks with a single daily status guard.
- [ ] Add age labels, including explicit unknown original posting dates, to concise role cards.
- [ ] Test empty/partial/all-source failure reports; replay guard; 13:00 India boundary; test isolation; acknowledged/rejected/ambiguous outcomes; existing criteria/dedup regressions. Run the complete offline suite.
- [ ] Publish reviewed code and dispatch the one labeled test once. Verify Telegram acknowledgment and final cache persistence; do not retry uncertain outcomes.
- [ ] Hand off exact workflow dispatch requirements for parent-side 13:00 scheduling; GitHub cron remains best-effort and cannot promise exact-time delivery.

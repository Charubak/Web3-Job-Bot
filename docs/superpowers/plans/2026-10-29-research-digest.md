# Daily Research Digest Implementation Plan

> **For agentic workers:** Execute inline with executing-plans; user already approved implementation and replacement. Do not cut over Telegram until the parent verifies the ChatGPT schedule.

**Goal:** Publish fresh, auditable public job evidence for a research-capable ChatGPT digest at13:00IST, while preserving filters, recency, history and reminders.

**Architecture:** Existing adapters remain deterministic collection. A separate no-secret collector produces public evidence, unresolved research tasks, per-request source health and historical posting dates. A GitHub workflow publishes only explicitly allowlisted public metadata on a dedicated data branch; delivery history and activation SQLite never leave private cache. ChatGPT researches the remaining uncertainties and maintains its delivery history. Telegram has a reversible repository-variable cutover switch.

**Tech Stack:** Python3.11+, unittest, httpx/BeautifulSoup, SQLite history, GitHub Actions and public JSON/Markdown.

### Task1: Geography and evidence regression

- [x] Add regression tests in tests/test_research_collector.py for Offchain's benefits/CA–CO exclusion, India exclusions, country-only hiring, hybrid controls and line boundaries.
- [x] Repair filters.py sentence normalization and geography classification. Positive exclusions unrelated to India must be explicitly marked for eligibility research instead of silently treating them as India-incompatible; structured country conflicts remain unresolved.
- [x] Preserve location/timezone eligibility evidence and list boundaries in boards.py; verify existing suite.

### Task2: Collection evidence and research output

- [x] Extend source_health.py with sanitized request/status telemetry and zero-result warnings, without retaining raw responses or credentials.
- [x] Add research_collector.py: collector-only entry point, source requests, rejection counts, detailed promising candidates, exact evidence/aliases, research queue, strict uncertain-role labels, persisted public history and all-source health. Never import notifier/config or modify delivery state.
- [x] Add meaningful tests for history persistence, report privacy, date merging, same-title distinct requisitions, stale/closed handling, public output boundaries and no-send behavior.

### Task3: Repair and broaden verified collectors

- [x] Inspect official company careers links for moved ATS endpoints; update only verified associations. Unsupported/blocking endpoints remain visible as incomplete.
- [x] Add bounded pagination for relevant board categories and an external-link evidence queue rather than falsely treating snippets as verified primary roles.
- [x] Emit adaptive social/company search tasks in output so the research digest cannot equate fixed Bing zeros with coverage.

### Task4: Publication and cutover

- [x] Add .github/workflows/research-collection.yml, daily12:30IST plus manual runs; test, collect without Telegram secrets, publish an allowlisted report/history to research-data branch, upload complete structured evidence artifact.
- [x] Keep workflow default permission read-only; scope data-branch write to publishing job only. No new credentials or account grants. Document public data schema and retention.
- [x] Add reversible JOB_DIGEST_DELIVERY=chatgpt workflow switch, covering Mac dispatches and cron fallbacks, with tests; keep current channel until parent verifies replacement. The active Mac LaunchAgent remains unchanged pending cutover.
- [x] Run full tests, compile/diff checks, real read-only benchmark and negative controls (568 tests passed).
- [ ] Commit/deploy to main (branch unprotected and existing account has push), verify production research run/public raw report, then report parent-ready cutover.

### Completion evidence

Record test count, deployed commit/run URLs, public latest.json/latest.md URLs, source failures, benchmark outcome and exact cutover action in final response. No Telegram test sends.

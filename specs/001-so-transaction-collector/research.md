# Research: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Phase**: 0 | **Date**: 2026-09-26

Decisions updated after user-approved clarifications on 2026-09-28. Live API acceptance remains separate from offline regression evidence.

---

## Decision Log

### D-001: Data Source — Stack Exchange API

- **Decision**: Use `api.stackexchange.com/2.3/questions` with the `stackoverflow` site parameter.
- **Rationale**: Official, stable API with structured JSON responses. Avoids HTML scraping and Terms of Service risk. Enforced by the project constitution.
- **Alternatives considered**: HTML scraping (forbidden by constitution), Stack Overflow Data Dump (batch-only, no incremental update path).
- **Key parameters**: `site=stackoverflow`, `pagesize=100`, `filter` to include `tags`, `question_id`, `title`, `creation_date`, `score`, `answer_count`.
- **Pagination signal**: `has_more` field in JSON response. `has_more: false` or empty `items` array signals end of current date partition.

### D-002: Date Partitioning — 7-Day Windows

- **Decision**: Segment the full question corpus into consecutive 7-day date ranges via `fromdate` / `todate` UNIX-timestamp parameters on the API.
- **Rationale**: Bounds each partition to a manageable page count (~tens of pages at pagesize=100). Uses the user-approved application cap of 25 pages per partition. Enables fine-grained checkpoint recovery at the partition level.
- **Alternatives considered**: Monthly partitions or a single crawl would sample too narrow a date range under our 25-page-per-partition policy.
- **Partition range**: Logical start inclusive and end exclusive; send API `todate=end-1 second` because both API bounds are inclusive. Partitions span from a configurable `START_DATE` forward until enough transactions are collected or all configured partitions are exhausted.
- **Application pagination cap**: Stop at 25 pages (up to 2,500 fetched questions per partition before filtering). If more questions remain, log that they are skipped and advance; dense weeks need not be fully covered. Do not subdivide partitions.

### D-003: Spider — Scrapy Spider (not CrawlSpider)

- **Decision**: Plain `scrapy.Spider` subclass: `StackOverFlowSpider`.
- **Rationale**: Crawl flow is fully controlled by the spider (partition list, page iteration, `has_more` follow). `CrawlSpider`'s rule-based link following adds unnecessary complexity for an API-only collector.
- **Responsibilities**: Build API URLs, iterate date partitions, follow `has_more` pagination, parse JSON responses, yield `StackOverFlowItem` objects. No filtering or persistence.

### D-004: Pagination — has_more Field

- **Decision**: After each page response, check `has_more` in the JSON body. If `True` and the current page < 25, request `page + 1` for the same partition. If `False` or `items` is empty, advance to the next partition.
- **Rationale**: `has_more` is the canonical Stack Exchange API pagination signal. Checking both `has_more` and empty `items` guards against edge cases where the field might be missing.
- **Page limit guard**: Never request page > 25 for any partition (user-approved sampling policy).

### D-005: Request-Level Resume — Scrapy JOBDIR and Safe Page Cursor

- **Decision**: Keep the Scrapy disk queue in JOBDIR and add an atomic, credential-free `collector.json` cursor managed outside the spider by `PageCheckpoint`.
- **Rationale**: A pending-request queue alone does not capture a request already in flight. Persist the current page before dispatch, then advance only after item completion signals confirm storage/drop completion.
- **Recovery**: Ignore requests from older process IDs; replay the trusted cursor. Missing/corrupt cursor or mismatched output/configuration falls back to the first partition. Unreadable native queue files are preserved; a memory queue plus the durable cursor is used. This replaces the earlier queue-only assumption.

### D-006: Item Pipelines — Priority Order

- **Decision**: Four pipelines in priority order per the project constitution:
  - **100 — TagValidationPipeline**: Validate a lowercase/trimmed/deduplicated view of tags; drop if fewer than two remain.
  - **200 — NormalizationPipeline**: Lowercase all tags; strip whitespace; convert `creation_date` UNIX timestamp to ISO-8601 string.
  - **300 — DeduplicationPipeline**: Check `question_id` against a persistent seen-set (NDJSON output file scan on startup + in-memory set during run). Drop duplicates.
  - **400 — StoragePipeline**: Append/flush/fsync valid items as NDJSON; acknowledge committed IDs to DeduplicationPipeline only after success.
- **Rationale**: Enforced by constitution. Strict ordering prevents invalid or duplicate records from ever reaching storage.

### D-007: Cross-Run Deduplication — NDJSON Output as Source of Truth

- **Decision**: At startup, `DeduplicationPipeline.open_spider()` scans the existing output file line-by-line, parses each JSON object, extracts `question_id`, and loads it into an in-memory `set`. During the run, every new item is checked against this set before writing.
- **Rationale**: No external database or secondary index needed. The output file IS the deduplication record. Loading into memory on startup is fast for up to ~200k integer IDs (memory proportional to the number of distinct IDs). Avoids the complexity of a separate SQLite or Redis store.
- **Startup target check (FR-015)**: `open_spider()` also counts the distinct IDs loaded; if the count is already >= 100,000, async start closes with "target already met" before any API requests.

### D-008: Output Format — NDJSON (JSON Lines)

- **Decision**: One JSON object per line, UTF-8 encoded, file extension `.jsonl`.
- **Rationale**: Append-safe (no need to rewrite the full file). Trivially scannable for deduplication and validation without loading the entire file. Compatible with downstream tooling (pandas, jq, etc.).
- **File path**: Configurable via `OUTPUT_FILE` in `settings.py`, defaulting to `output/transactions.jsonl`.

### D-009: API Key Injection — Downloader Middleware

- **Decision**: `StackOverFlowDownloaderMiddleware` appends the `key` query parameter from `os.environ["STACKOVERFLOW_API_KEY"]` to every outgoing request.
- **Rationale**: Constitution forbids hardcoded credentials. Middleware is the correct Scrapy separation-of-concerns layer for request mutation. Fail-fast: middleware `__init__` raises `RuntimeError` if the env var is absent.
- **Authenticated quota**: 10,000 requests/day with a registered app key. At pagesize=100 and ~25 pages/partition, that is ~400 partitions per day — sufficient to reach 100k transactions in 1–3 days of crawl time.

### D-010: Retry & Backoff

- **Decision**: Disable built-in RetryMiddleware and implement bounded retries in the authentication/downloader middleware using Scrapy's retry-request helper. Default is five retries after the initial request; exponential delays are 1, 2, 4, 8, 16 seconds. Wait at least API backoff or numeric Retry-After when greater.
- **Errors**: Retry transient HTTP errors and API error IDs 500/502/503. API error ID 502 means throttle violation, not necessarily daily quota exhaustion. Zero remaining quota or an explicit quota-exhaustion message stops without retry. Preserve valid items in successful quota-zero responses before stopping.
- **Credential preflight**: `/info?site=stackoverflow` validates a supplied key before requesting question pages. Missing credentials fail locally. Use the standard `default` response filter instead of the earlier unverified opaque custom filter.

### D-011: Output File Corruption Recovery

- **Decision**: At `open_spider()`, after loading seen IDs, the pipeline truncates the output file to the byte offset of the last successfully parsed line. Only a malformed final line is removed. Schema errors, duplicates or non-final corrupt lines are fatal; a valid final line missing a newline is preserved.
- **Rationale**: Guarantees the output file is always valid NDJSON at startup. Prevents a partial write from blocking future appends.

### D-012: Validation Utility

- **Decision**: A standalone script `tools/validate_output.py` (not a Scrapy component) reads the output NDJSON file, reports: total lines, distinct `question_id`s, lines with < 2 tags, and whether the distinct count meets the 100k target.
- **Rationale**: Separates validation from the crawl runtime. Can be run at any time against any output file without starting Scrapy.

## Primary sources checked during remediation

- [API paging](https://api.stackexchange.com/docs/paging): pagesize up to 100 and has_more; 25 is our policy.
- [API throttles](https://api.stackexchange.com/docs/throttle): honor the backoff field before calling the same method again.
- [API errors](https://api.stackexchange.com/docs/error-handling): distinguish API error IDs from HTTP status and quota state.
- [Query bounds](https://api.stackexchange.com/docs/min-max): fromdate/todate are inclusive.

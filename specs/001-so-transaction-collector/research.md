# Research: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Phase**: 0 | **Date**: 2026-09-26

All technical choices were explicitly specified by the user in the `/speckit-plan` invocation. No open unknowns remain. This document records the decisions and their rationale.

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
- **Rationale**: Bounds each partition to a manageable page count (~tens of pages at pagesize=100). Avoids hitting the Stack Exchange API's 25-page hard limit on deep pagination. Enables fine-grained checkpoint recovery at the partition level.
- **Alternatives considered**: Monthly partitions (too large; risk hitting API pagination cap), no partitioning / single crawl (hits 25-page API limit and cannot reach 100k records).
- **Partition range**: `fromdate` inclusive, `todate` exclusive. Partitions span from a configurable `START_DATE` forward until enough transactions are collected or all configured partitions are exhausted.
- **API pagination cap**: Stack Exchange API allows a maximum of 25 pages per query. At pagesize=100 that is 2,500 questions per partition per unfiltered call. With 7-day windows this is well within range for active periods.

### D-003: Spider — Scrapy Spider (not CrawlSpider)

- **Decision**: Plain `scrapy.Spider` subclass: `StackOverFlowSpider`.
- **Rationale**: Crawl flow is fully controlled by the spider (partition list, page iteration, `has_more` follow). `CrawlSpider`'s rule-based link following adds unnecessary complexity for an API-only collector.
- **Responsibilities**: Build API URLs, iterate date partitions, follow `has_more` pagination, parse JSON responses, yield `StackOverFlowItem` objects. No filtering or persistence.

### D-004: Pagination — has_more Field

- **Decision**: After each page response, check `has_more` in the JSON body. If `True` and the current page < 25, request `page + 1` for the same partition. If `False` or `items` is empty, advance to the next partition.
- **Rationale**: `has_more` is the canonical Stack Exchange API pagination signal. Checking both `has_more` and empty `items` guards against edge cases where the field might be missing.
- **Page limit guard**: Never request page > 25 for any partition (API hard limit).

### D-005: Request-Level Resume — Scrapy JOBDIR

- **Decision**: Use Scrapy's `JOBDIR` setting to persist the request queue and spider state.
- **Rationale**: Native, battle-tested Scrapy mechanism. On restart Scrapy replays the pending request queue, ensuring the spider resumes from the correct partition and page without custom checkpoint logic in the spider itself.
- **Limitation**: `JOBDIR` persists pending requests, not completed ones. Deduplication via `question_id` provides the safety net for any overlap at the resume boundary.

### D-006: Item Pipelines — Priority Order

- **Decision**: Four pipelines in priority order per the project constitution:
  - **100 — TagValidationPipeline**: Drop items with < 2 distinct tags (after deduplicating the tag list).
  - **200 — NormalizationPipeline**: Lowercase all tags; strip whitespace; convert `creation_date` UNIX timestamp to ISO-8601 string.
  - **300 — DeduplicationPipeline**: Check `question_id` against a persistent seen-set (NDJSON output file scan on startup + in-memory set during run). Drop duplicates.
  - **400 — StoragePipeline**: Append valid, unique items as NDJSON lines to the output file.
- **Rationale**: Enforced by constitution. Strict ordering prevents invalid or duplicate records from ever reaching storage.

### D-007: Cross-Run Deduplication — NDJSON Output as Source of Truth

- **Decision**: At startup, `DeduplicationPipeline.open_spider()` scans the existing output file line-by-line, parses each JSON object, extracts `question_id`, and loads it into an in-memory `set`. During the run, every new item is checked against this set before writing.
- **Rationale**: No external database or secondary index needed. The output file IS the deduplication record. Loading into memory on startup is fast for up to ~200k integer IDs (< 5 MB RAM). Avoids the complexity of a separate SQLite or Redis store.
- **Startup target check (FR-015)**: `open_spider()` also counts the distinct IDs loaded; if the count is already >= 100,000, the spider is closed immediately with a "target already met" status.

### D-008: Output Format — NDJSON (JSON Lines)

- **Decision**: One JSON object per line, UTF-8 encoded, file extension `.jsonl`.
- **Rationale**: Append-safe (no need to rewrite the full file). Trivially scannable for deduplication and validation without loading the entire file. Compatible with downstream tooling (pandas, jq, etc.).
- **File path**: Configurable via `OUTPUT_FILE` in `settings.py`, defaulting to `output/transactions.jsonl`.

### D-009: API Key Injection — Downloader Middleware

- **Decision**: `StackOverFlowDownloaderMiddleware` appends the `key` query parameter from `os.environ["STACKOVERFLOW_API_KEY"]` to every outgoing request.
- **Rationale**: Constitution forbids hardcoded credentials. Middleware is the correct Scrapy separation-of-concerns layer for request mutation. Fail-fast: middleware `__init__` raises `RuntimeError` if the env var is absent.
- **Authenticated quota**: 10,000 requests/day with a registered app key. At pagesize=100 and ~25 pages/partition, that is ~400 partitions per day — sufficient to reach 100k transactions in 1–3 days of crawl time.

### D-010: Retry & Back-off — Scrapy RetryMiddleware

- **Decision**: Enable Scrapy's built-in `RetryMiddleware` with `RETRY_TIMES = 5` and exponential back-off via `RETRY_HTTP_CODES` + custom `DOWNLOAD_DELAY` scaling. Quota exhaustion (HTTP 400 with `error_id: 502`) is treated separately: detected in the spider's `errback` / response parser and triggers a clean shutdown.
- **Rationale**: Scrapy's `RetryMiddleware` handles transient errors natively. Quota exhaustion is a distinct, non-retriable condition and must not consume retry budget.

### D-011: Output File Corruption Recovery

- **Decision**: At `open_spider()`, after loading seen IDs, the pipeline truncates the output file to the byte offset of the last successfully parsed line. Any trailing partial line is removed.
- **Rationale**: Guarantees the output file is always valid NDJSON at startup. Prevents a partial write from blocking future appends.

### D-012: Validation Utility

- **Decision**: A standalone script `tools/validate_output.py` (not a Scrapy component) reads the output NDJSON file, reports: total lines, distinct `question_id`s, lines with < 2 tags, and whether the distinct count meets the 100k target.
- **Rationale**: Separates validation from the crawl runtime. Can be run at any time against any output file without starting Scrapy.

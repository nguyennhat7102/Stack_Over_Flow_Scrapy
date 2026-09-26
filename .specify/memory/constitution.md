# StackOverFlow Crawler Constitution

## Core Principles

### I. Scrapy Separation of Concerns (NON-NEGOTIABLE)
Each Scrapy component has one and only one domain of responsibility:

- **Spiders** (`StackOverFlow/spiders/`): Own the crawl flow and data parsing. Spiders yield `StackOverflowItem` objects — they do not validate, deduplicate, or persist data.
- **Pipelines** (`pipelines.py`): Own validation, normalization, deduplication, and storage. No HTTP logic belongs here.
- **Items** (`items.py`): Define the data contract — the shape of a scraped object. No business logic.
- **Downloader/Spider Middlewares** (`middlewares.py`): Own request-level concerns — rate-limiting, retries, authentication headers, and API key injection. No parsing logic.

### II. Transaction Model
Every Stack Overflow question maps to exactly one **transaction**:

- `question_id` is the **unique transaction identifier** — it must never be duplicated in the output dataset.
- The **tags** of a question are the **items** in that transaction.
- A transaction is **valid** only if it contains **≥ 2 distinct tags**.
- Transactions with 0 or 1 tag must be **silently dropped** by the pipeline, never written to storage.

### III. Pause & Resume Safety (NON-NEGOTIABLE)
The crawler must support safe pause and resume without producing duplicate records:

- Use Scrapy's built-in **job persistence** (`JOBDIR` setting) to checkpoint crawl state.
- The deduplication pipeline stage must use `question_id` as the idempotency key, checking against already-persisted records before writing.
- Any restart of a crawl **must not re-insert** a `question_id` that already exists in the output.

### IV. API Key Security
All credentials are injected from the environment — never hardcoded:

- Stack Overflow API keys are read via `os.environ` (e.g., `STACKOVERFLOW_API_KEY`) inside `settings.py` or the relevant middleware.
- The project must fail fast with a clear error message at startup if a required key is missing.
- Keys must never appear in logs, output files, or version control.

### V. No Browser Automation or HTML Scraping
This project uses the **Stack Overflow API** as its sole data source:

- Browser automation tools (Selenium, Playwright, Splash, Zyte) are **forbidden** unless explicitly approved.
- HTML scraping of `stackoverflow.com` is **forbidden**.
- All requests target the official Stack Exchange API (`api.stackexchange.com`).

### VI. Incremental Scale — Validate Before Expanding
Crawls must progress through validated stages:

1. **Pilot run**: ≤ 500 questions. Validate output schema, deduplication, and tag filtering.
2. **Small batch**: ≤ 5,000 questions. Validate pipeline throughput and pause/resume.
3. **Full scale**: 100,000+ questions. **Only permitted after Stages 1 & 2 pass acceptance criteria.**

Do not skip stages. Each stage gate requires observable, measurable evidence of correctness.

### VII. Acceptance Criteria Must Be Observable
Every deployment stage must define acceptance criteria that can be verified without manual inspection of raw data:

- Record counts are logged and match expected ranges.
- Duplicate `question_id` count is **0** after each run (verifiable via a SQL `GROUP BY` or set-difference check).
- Dropped transaction count (< 2 tags) is reported per run.
- API quota consumption is logged per run.

## Data Contract

| Field | Type | Source | Notes |
|---|---|---|---|
| `question_id` | `int` | API response | Primary key / transaction ID |
| `tags` | `list[str]` | API response | Must have ≥ 2 items to be valid |
| `title` | `str` | API response | Optional context field |
| `creation_date` | `int` | API response | Unix timestamp |
| `score` | `int` | API response | Optional context field |
| `answer_count` | `int` | API response | Optional context field |

## Pipeline Ordering

Pipelines execute in priority order (lower number = earlier):

| Priority | Class | Responsibility |
|---|---|---|
| 100 | `TagValidationPipeline` | Drop items with < 2 tags |
| 200 | `NormalizationPipeline` | Normalize/clean field values |
| 300 | `DeduplicationPipeline` | Drop items with a seen `question_id` |
| 400 | `StoragePipeline` | Persist valid, unique items to output |

## Constraints & Forbidden Patterns

- **No** hardcoded API keys, tokens, or credentials anywhere in source files.
- **No** HTML parsing (`BeautifulSoup`, `lxml` selectors on HTML pages) unless explicitly approved.
- **No** bypassing the `TagValidationPipeline` — spiders must never filter tags themselves.
- **No** writing to storage from a Spider — all persistence is the pipeline's responsibility.
- **No** scaling to 100k items before pilot and small-batch stages are signed off.

## Governance

This constitution supersedes all other coding conventions for this project.

- All changes to pipeline ordering, data contract fields, or crawl scale limits require a documented amendment below.
- PRs must include evidence of acceptance criteria being met (log excerpts, record counts, duplicate checks).
- Amendments must specify: the change, the rationale, and the date.

### Amendment Log
*(No amendments yet.)*

---

**Version**: 1.0.0 | **Ratified**: 2026-09-26 | **Last Amended**: 2026-09-26

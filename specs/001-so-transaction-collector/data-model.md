# Data Model: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Phase**: 1 | **Date**: 2026-09-26

---

## Entities

### 1. StackOverFlowItem (Scrapy Item / Data Transfer Object)

The in-flight data object produced by the spider and processed by the pipeline chain.

| Field | Python Type | Source | Required | Notes |
|---|---|---|---|---|
| `question_id` | `int` | API `items[].question_id` | Yes | Primary key / dedup key |
| `tags` | `list[str]` | API `items[].tags` | Yes | Raw tag list from API; may contain duplicates |
| `title` | `str` | API `items[].title` | Yes | Human-readable context |
| `creation_date` | `int` | API `items[].creation_date` | Yes | UNIX timestamp from API |
| `score` | `int` | API `items[].score` | No | Optional context; default 0 |
| `answer_count` | `int` | API `items[].answer_count` | No | Optional context; default 0 |

**Defined in**: `StackOverFlow/items.py` as a `@dataclass`.

---

### 2. Transaction (Output Record)

A valid, normalized, deduplicated record written to the NDJSON output file. This is the final form of a `StackOverFlowItem` after passing all pipelines.

| Field | JSON Type | Derived From | Notes |
|---|---|---|---|
| `question_id` | `number` | Item `question_id` | Integer; unique across output file |
| `tags` | `array[string]` | Item `tags` after normalization | Lowercased, whitespace-stripped, deduplicated within question; ≥ 2 items |
| `title` | `string` | Item `title` | Unchanged |
| `creation_date` | `string` | Item `creation_date` | ISO-8601 UTC format: `"YYYY-MM-DDTHH:MM:SSZ"` |
| `score` | `number` | Item `score` | Integer |
| `answer_count` | `number` | Item `answer_count` | Integer |

**Stored in**: `output/transactions.jsonl` (one JSON object per line).

---

### 3. DatePartition (Logical Crawl Unit)

A 7-day time window used to segment the API query space.

| Attribute | Type | Notes |
|---|---|---|
| `fromdate` | `int` (UNIX timestamp) | Inclusive start of partition window |
| `todate` | `int` (UNIX timestamp) | Exclusive end (`fromdate + 7 * 86400`) |
| `current_page` | `int` | Current page being fetched (1-indexed) |
| `is_complete` | `bool` | True when `has_more` is False or `items` is empty or `current_page` == 25 |

**Lives in**: Spider memory only (generated at `start_requests()`); persisted implicitly via JOBDIR request queue.

---

### 4. CrawlCheckpoint (Scrapy JOBDIR State)

Scrapy's native job persistence directory. Contains the pending request queue and spider state.

| File | Contents |
|---|---|
| `<JOBDIR>/requests.queue/` | Serialized pending API page requests |
| `<JOBDIR>/spider.state` | Spider `state` dict (used for cross-request counters if needed) |

**Location**: Configurable via `JOBDIR` setting; default `crawl_jobs/stackoverflow`.

---

### 5. SeenIDSet (Runtime Deduplication State)

In-memory `set[int]` holding all `question_id`s already present in the output file. Built at `open_spider()` by scanning the existing output file.

| Attribute | Type | Notes |
|---|---|---|
| `ids` | `set[int]` | In-memory only; rebuilt from output file on every startup |
| `size` | `int` | Number of distinct IDs loaded; checked against 100k target at startup |

**Owned by**: `DeduplicationPipeline`.

---

## Validation Rules

Enforced by pipelines in strict priority order:

| Rule | Pipeline | Condition | Action |
|---|---|---|---|
| Minimum tag count | TagValidationPipeline (100) | `len(set(item.tags)) < 2` | `raise DropItem` |
| Tag deduplication | TagValidationPipeline (100) | Applied before count check | Deduplicate tag list in-place |
| Tag normalisation | NormalizationPipeline (200) | Always | Lowercase + strip whitespace on each tag |
| Date conversion | NormalizationPipeline (200) | Always | `creation_date` int → ISO-8601 UTC string |
| Duplicate question | DeduplicationPipeline (300) | `question_id` in `seen_ids` | `raise DropItem` |
| Record persistence | StoragePipeline (400) | Item passed all above | Append NDJSON line to output file |

---

## State Transitions: Transaction Lifecycle

```
API Response JSON
       │
       ▼
 Spider parses items[] ──► yield StackOverFlowItem
                                      │
                           ┌──────────▼──────────┐
                           │ TagValidationPipeline│
                           │ (priority 100)       │
                           │  set(tags) < 2?      │
                           │  YES → DropItem      │
                           └──────────┬───────────┘
                                      │ NO (≥2 distinct tags)
                           ┌──────────▼──────────┐
                           │ NormalizationPipeline│
                           │ (priority 200)       │
                           │  lowercase tags      │
                           │  strip whitespace    │
                           │  date → ISO-8601     │
                           └──────────┬───────────┘
                                      │
                           ┌──────────▼──────────┐
                           │ DeduplicationPipeline│
                           │ (priority 300)       │
                           │  question_id in set? │
                           │  YES → DropItem      │
                           └──────────┬───────────┘
                                      │ NO (unique)
                           ┌──────────▼──────────┐
                           │   StoragePipeline    │
                           │   (priority 400)     │
                           │   append NDJSON line │
                           │   add ID to seen set │
                           └─────────────────────┘
                                  Transaction
                               written to disk
```

---

## File Layout (Source Code)

```text
StackOverFlow/                         # Scrapy project package
├── __init__.py
├── items.py                           # StackOverFlowItem dataclass
├── middlewares.py                     # StackOverFlowDownloaderMiddleware (API key)
├── pipelines.py                       # TagValidation, Normalization, Dedup, Storage
├── settings.py                        # JOBDIR, ITEM_PIPELINES, DOWNLOAD_DELAY, env vars
└── spiders/
    └── stackoverflow_spider.py        # StackOverFlowSpider (date-partition + pagination)

output/
└── transactions.jsonl                 # NDJSON output (created at first run)

crawl_jobs/
└── stackoverflow/                     # JOBDIR checkpoint directory

tools/
└── validate_output.py                 # Standalone post-run validation script
```

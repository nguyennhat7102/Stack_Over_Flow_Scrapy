# Tasks: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Date**: 2026-09-26
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data Model**: [data-model.md](data-model.md)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directories, install dependencies, and establish the runtime environment the spider and pipelines depend on.

- [X] T001 Create `output/` and `crawl_jobs/stackoverflow/` directories; add `.gitkeep` files; add `output/*.jsonl` and `crawl_jobs/` to `.gitignore`
- [X] T002 Create `tools/` directory; add empty `tools/__init__.py`
- [X] T003 [P] Verify Scrapy and itemadapter are present in the environment; document exact versions in a `requirements.txt` at the project root

**Checkpoint**: Directories exist; `scrapy version` runs without error.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data contract, credentials guard, and settings. Every subsequent phase depends on these being complete.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Implement `StackOverFlowItem` dataclass in `StackOverFlow/items.py` with fields: `question_id: int`, `tags: list[str]`, `title: str`, `creation_date: int`, `score: int = 0`, `answer_count: int = 0` — replace the empty `pass` body; no business logic
- [X] T005 Implement `StackOverFlowDownloaderMiddleware` in `StackOverFlow/middlewares.py`: read `STACKOVERFLOW_API_KEY` from `os.environ` in `__init__`; raise `RuntimeError` with a clear message if absent or empty; append `key=<value>` to every outgoing request's query string in `process_request`; never log the key value
- [X] T006 Update `StackOverFlow/settings.py`: set `ROBOTSTXT_OBEY = False`; configure `CONCURRENT_REQUESTS_PER_DOMAIN = 1` and `DOWNLOAD_DELAY = 1`; add `RETRY_TIMES = 5` and `RETRY_HTTP_CODES = [429, 500, 502, 503, 504]`; set `JOBDIR = "crawl_jobs/stackoverflow"`; set `OUTPUT_FILE = "output/transactions.jsonl"`; set `PARTITION_START_DATE = "2008-08-01"` and `PARTITION_END_DATE = ""` (empty = today); set `TARGET_RECORDS = 100000`; enable `DOWNLOADER_MIDDLEWARES` with `StackOverFlowDownloaderMiddleware` at priority 543; enable `ITEM_PIPELINES` with `TagValidationPipeline: 100`, `NormalizationPipeline: 200`, `DeduplicationPipeline: 300`, `StoragePipeline: 400`; set `FEED_EXPORT_ENCODING = "utf-8"`
- [X] T007 [P] Write `tools/validate_output.py`: accept `--file` argument (default `output/transactions.jsonl`); scan NDJSON file line-by-line; collect `question_id` values into a set; count total lines, distinct IDs, duplicate IDs, records with `len(tags) < 2`; print the validation report table; exit with code 0 if distinct IDs >= 100,000 and duplicate_count == 0 and under_tagged == 0, else exit 1

**Checkpoint**: `python tools/validate_output.py --file /dev/null` exits with code 1 and prints a report showing 0 lines; `scrapy check` finds the middleware without error.

---

## Phase 3: User Story 1 — Launch Initial Data Collection (Priority: P1) 🎯 MVP

**Goal**: The spider can fetch questions from a single date partition, filter by tag count, and write valid transactions to the NDJSON output file.

**Independent Test**: Run `scrapy crawl stackoverflow -s PARTITION_START_DATE=2023-01-01 -s PARTITION_END_DATE=2023-01-08 -s TARGET_RECORDS=500 -s OUTPUT_FILE=output/pilot.jsonl -s JOBDIR=crawl_jobs/pilot`; then `python tools/validate_output.py --file output/pilot.jsonl`; confirm at least 1 record, 0 duplicates, 0 under-tagged records, valid NDJSON.

### Implementation for User Story 1

- [X] T008 [US1] Implement `TagValidationPipeline` class in `StackOverFlow/pipelines.py` (priority 100): in `process_item`, build `distinct_tags = set(item.tags)`; if `len(distinct_tags) < 2`, raise `DropItem`; otherwise return item unchanged; no normalization here
- [X] T009 [US1] Implement `NormalizationPipeline` class in `StackOverFlow/pipelines.py` (priority 200): in `process_item`, apply `tag.lower().strip()` to every tag; deduplicate the normalized list while preserving first-occurrence order; replace `item.tags` with the deduplicated normalized list; convert `item.creation_date` (int UNIX timestamp) to ISO-8601 UTC string (`"YYYY-MM-DDTHH:MM:SSZ"`); return item
- [X] T010 [US1] Implement `DeduplicationPipeline` class in `StackOverFlow/pipelines.py` (priority 300): in `open_spider`, call the shared `_load_seen_ids(output_path)` helper (T011) to populate `self.seen_ids: set[int]`; also check `len(self.seen_ids) >= spider.settings.getint("TARGET_RECORDS", 100000)` — if true, log "TARGET ALREADY MET" and call `spider.crawler.engine.close_spider(spider, "target_already_met")`; in `process_item`, if `item.question_id` in `self.seen_ids`, raise `DropItem`; otherwise add to `self.seen_ids` and return item
- [X] T011 [US1] Implement `_load_seen_ids(output_path: str) -> set[int]` module-level helper in `StackOverFlow/pipelines.py`: open the file for reading (if it exists); iterate lines; attempt `json.loads(line)` to extract `question_id`; on `json.JSONDecodeError` for the **last line**, truncate the file to that line's start offset and log the removal; on `json.JSONDecodeError` for any **non-last line**, raise `RuntimeError` with a message identifying the corrupt line number and instructing the user to inspect the file before retrying — do not skip or continue past mid-file corruption
- [X] T012 [US1] Implement `StoragePipeline` class in `StackOverFlow/pipelines.py` (priority 400): in `open_spider`, open `OUTPUT_FILE` in append mode with `encoding="utf-8"`, `newline="\n"`; in `process_item`, serialize item to a dict, call `json.dumps(record, ensure_ascii=False)`, write the JSON string followed by `"\n"`, and `flush()` after each write; in `close_spider`, close the file handle; add `item.question_id` to `DeduplicationPipeline.seen_ids` via spider reference is NOT needed here — `DeduplicationPipeline` owns the seen set
- [X] T013 [US1] Create `StackOverFlow/spiders/stackoverflow_spider.py` with class `StackOverFlowSpider(scrapy.Spider)`, `name = "stackoverflow"`: implement `start_requests` to generate the list of 7-day date partitions from `PARTITION_START_DATE` to `PARTITION_END_DATE` (or today if empty); for each partition yield the first API request to `https://api.stackexchange.com/2.3/questions?site=stackoverflow&pagesize=100&page=1&fromdate=<unix>&todate=<unix>&filter=!6WPIomnMOOD*e&order=asc&sort=creation`; store partition metadata in `request.meta`
- [X] T014 [US1] Implement `parse` callback in `StackOverFlowSpider`: parse JSON response body; for each item in `response.json()["items"]`, yield a `StackOverFlowItem(question_id=..., tags=..., title=..., creation_date=..., score=..., answer_count=...)`; check `has_more` field — if `True` and `meta["page"] < 25`, yield a new request for `page + 1` of the same partition; if `False` or `items` is empty, log partition complete and the spider's `start_requests` loop advances to the next partition naturally via the request queue; log per-page progress line: `page=<N> partition=<from>/<to> fetched=<N> written=<N> dropped=<N> total_written=<N>`
- [X] T015 [US1] Implement quota-exhaustion detection in `StackOverFlowSpider`: in `parse`, check if `response.status == 400` and `response.json().get("error_id") == 502`; if so, log `QUOTA EXHAUSTED` with `quota_remaining` and `backoff` values from the response body; call `self.crawler.engine.close_spider(self, "quota_exhausted")` to trigger a clean shutdown; preserve JOBDIR checkpoint
- [X] T016 [US1] Implement `close` callback in `StackOverFlowSpider`: emit the FINAL SUMMARY log line: `partitions_processed=<N> pages_consumed=<N> total_fetched=<N> total_written=<N> total_dropped=<N>`; read `quota_remaining` from the last API response if available

**Checkpoint (US1)**: Pilot run produces ≥ 1 record in `output/pilot.jsonl`; `python tools/validate_output.py --file output/pilot.jsonl` exits 1 (< 100k) but reports 0 duplicates and 0 under-tagged; log shows per-page lines and FINAL SUMMARY.

---

## Phase 4: User Story 2 — Pause and Resume Collection (Priority: P2)

**Goal**: Interrupting and restarting the crawler produces no duplicate `question_id`s and resumes from the correct page and partition.

**Independent Test**: Run pilot to ~500 records; send SIGINT; restart with same settings; run `python tools/validate_output.py`; assert duplicate count = 0 and total_written > 500.

### Implementation for User Story 2

- [X] T017 [US2] Verify `_load_seen_ids` (T011) is called in `DeduplicationPipeline.open_spider` on every startup — confirm it rebuilds the full seen-ID set from the existing output file before any new items are processed; add a startup log line: `Loaded <N> seen question_ids from <path>`
- [X] T018 [US2] Implement missing/corrupt-JOBDIR fallback in `StackOverFlowSpider.start_requests`: detect if the JOBDIR is absent or empty (no `requests.queue` sub-directory); if so, log a WARNING that no checkpoint was found and collection will start from the first uncompleted partition; do NOT attempt to infer a page offset from an untrusted source — rely on deduplication for idempotency
- [X] T019 [US2] Add FR-015 target-already-met guard to `DeduplicationPipeline.open_spider` (should already be present from T010): confirm the guard runs before the spider makes any API requests, and that it logs `TARGET ALREADY MET | distinct_ids=<N> >= target=<M>` before triggering clean shutdown

**Checkpoint (US2)**: After interrupt + restart, `distinct_ids == total_lines` (0 duplicates); the log shows `Loaded <N> seen question_ids` on the resumed run.

---

## Phase 5: User Story 3 — Observe Progress and Validate Output (Priority: P3)

**Goal**: Per-page log lines advance monotonically; the final summary is emitted; the validation utility reports a correct, machine-readable result.

**Independent Test**: Run a small-batch crawl; tail the log; confirm page numbers increment per partition; confirm FINAL SUMMARY appears at exit; run `python tools/validate_output.py --file output/small_batch.jsonl` and confirm the report matches the log counts.

### Implementation for User Story 3

- [X] T020 [P] [US3] Add per-page counters to `StackOverFlowSpider`: maintain `self.total_fetched`, `self.total_written`, `self.total_dropped` as instance attributes; increment `total_fetched` in `parse` for each item in `response.json()["items"]`; increment `total_written` / `total_dropped` via spider stats (`self.crawler.stats`) or direct counter — confirm the per-page log line in `parse` uses these values
- [X] T021 [P] [US3] Add summary report emission to `StoragePipeline.close_spider` or `StackOverFlowSpider.close`: ensure the FINAL SUMMARY log line includes `total_written` matching the actual number of lines in the output file (cross-check by counting lines in `close_spider`)
- [X] T022 [US3] Complete `tools/validate_output.py` (T007 foundation): ensure it handles an empty file gracefully (reports 0 everywhere); ensure it reports `Target (100,000) met: YES/NO`; ensure exit code 0 only when target met AND 0 duplicates AND 0 under-tagged; ensure exit code 1 in all other cases with a ❌ summary line

**Checkpoint (US3)**: `python tools/validate_output.py --file output/pilot.jsonl` prints a correctly formatted report; counts match the FINAL SUMMARY in the Scrapy log.

---

## Phase 6: User Story 4 — Reach 100,000+ Valid Transactions (Priority: P1)

**Goal**: The system accumulates ≥ 100,000 distinct valid transactions across one or more runs, stopping immediately at the target without completing the current partition.

**Independent Test**: Run full-scale crawl across multiple partitions (may span multiple sessions); run `python tools/validate_output.py --file output/transactions.jsonl`; assert distinct_ids ≥ 100,000, duplicates = 0, under_tagged = 0.

### Implementation for User Story 4

- [X] T023 [US4] Implement target-reached early-stop in `DeduplicationPipeline.process_item`: after adding a new `question_id` to `self.seen_ids`, check `len(self.seen_ids) >= self.target`; if true, log `TARGET REACHED | distinct_ids=<N>` and call `spider.crawler.engine.close_spider(spider, "target_reached")` — this fires the clean shutdown path immediately, without waiting for the current page or partition to complete (satisfies FR-020)
- [X] T024 [US4] Add three-stage partition guard to `StackOverFlowSpider` or a management wrapper script `tools/run_stages.sh`: Stage 1 — set `TARGET_RECORDS=500`; Stage 2 — set `TARGET_RECORDS=5000`; Stage 3 — set `TARGET_RECORDS=100000`; document that each stage requires manual sign-off before the next is run (per the project constitution)
- [X] T025 [P] [US4] Update `tools/validate_output.py` to add `--target` flag (default 100000) so stage-gate validation reports pass/fail against the correct target per stage

**Checkpoint (US4)**: After `tools/validate_output.py` exits 0, the dataset is the primary deliverable. Log shows `TARGET REACHED` before FINAL SUMMARY on the completing run.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Hardening, recovery paths, and documentation completeness.

- [X] T026 [P] Add `errback` handler to all spider requests in `StackOverFlowSpider`: log the failure with page, partition, and exception; for `HttpError` with status 400 (quota exhaustion), route to the quota-exhaustion path (T015); for other `HttpError`, let `RetryMiddleware` handle; for `DNSLookupError` / `TCPTimedOutError`, log and let retry handle
- [X] T027 [P] Validate UTF-8 encoding of `StoragePipeline`: confirm the file is opened with `encoding="utf-8"` and `ensure_ascii=False` is passed to `json.dumps`; confirm no `latin-1` or default encoding fallback exists in any write path
- [X] T028 Add startup integrity check to `StoragePipeline.open_spider` or `DeduplicationPipeline.open_spider` that calls `_load_seen_ids` (which already truncates trailing corrupt lines per T011); add a log line confirming whether truncation occurred: `Output file integrity: OK` or `Output file integrity: truncated <N> bytes of corrupt trailing data`
- [X] T029 [P] Write `tools/run_stages.sh`: a shell wrapper that sequentially runs Stage 1 (≤500), waits for user confirmation, runs Stage 2 (≤5000), waits for user confirmation, then runs Stage 3 (100k+); each stage calls `python tools/validate_output.py --target <N>` after the crawl exits and aborts if validation fails
- [X] T030 [P] Update `specs/001-so-transaction-collector/quickstart.md` to reference `tools/run_stages.sh` in Stage 1/2/3 commands; add the `tools/validate_output.py --target` flag usage

**Checkpoint (Polish)**: `scrapy crawl stackoverflow --help` shows no errors; all pipeline classes have docstrings describing their single responsibility; `grep -r "STACKOVERFLOW_API_KEY" StackOverFlow/` shows the key is only read in `middlewares.py`; `grep -rn "hardcoded\|api_key\s*=" StackOverFlow/` returns nothing suspicious.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — **BLOCKS all user stories**
- **Phase 3 (US1 — Initial Collection)**: Depends on Phase 2; delivers the MVP
- **Phase 4 (US2 — Resume)**: Depends on Phase 3 (resume needs a working baseline crawler)
- **Phase 5 (US3 — Observability)**: Depends on Phase 3; can overlap with Phase 4
- **Phase 6 (US4 — 100k Scale)**: Depends on Phases 3 + 4 passing acceptance criteria
- **Phase 7 (Polish)**: Depends on all story phases being complete

### Within Each Phase — Execution Order

```
T004 (Item) ──► T005 (Middleware) ──► T006 (Settings)
                                          │
                     ┌────────────────────┘
                     ▼
              T007 (validate_output.py — can be parallel with T008–T012)
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
     T008 (TagVal) T009 (Norm) T010 (Dedup) ◄── T011 (helper) ◄── T012 (Storage)
         └───────────┴───────────┘
                     │
              T013 (Spider start_requests)
                     │
              T014 (parse callback)
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
    T015 (quota detect)   T016 (close/summary)
```

### Parallel Opportunities

- T003, T007 — can run alongside T004–T006
- T008, T009, T010/T011 — pipeline classes are independent files within `pipelines.py`; T010 depends on T011 being defined first
- T020, T021, T022 (US3) — all independently modifiable
- T026, T027, T029, T030 (Polish) — all touch different files

---

## Parallel Execution Examples

```bash
# Phase 2 parallelizable block (after T004 is done):
Task: "T005 — Implement StackOverFlowDownloaderMiddleware in StackOverFlow/middlewares.py"
Task: "T007 — Write tools/validate_output.py"   # independent of middleware

# Phase 3 pipeline block (after T006 settings done):
Task: "T008 — TagValidationPipeline in StackOverFlow/pipelines.py"
Task: "T009 — NormalizationPipeline in StackOverFlow/pipelines.py"
# T011 must be written before T010 references it
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup)
2. Complete Phase 2 (Foundational) — **do not skip**
3. Complete Phase 3 (US1) through T016
4. **STOP and run pilot**: `scrapy crawl stackoverflow -s TARGET_RECORDS=500 ...`
5. Run `python tools/validate_output.py --file output/pilot.jsonl --target 500`
6. If validation passes → MVP is done → proceed to Phase 4

### Incremental Delivery

1. Setup + Foundational → environment ready
2. Phase 3 (US1) → pilot crawl works → **MVP**
3. Phase 4 (US2) → resume without duplicates → **resumable MVP**
4. Phase 5 (US3) → logs and validation utility complete → **observable MVP**
5. Phase 6 (US4) → full 100k run → **primary deliverable**
6. Phase 7 (Polish) → production hardening → **final state**

---

## Notes

- `[P]` tasks touch different files and have no blocking dependency on each other — safe to run in parallel
- `[USN]` label maps each task to the user story it delivers
- Commit after each checkpoint to preserve a known-good state
- Never scale to Stage 3 (100k) before Stage 1 and Stage 2 have both passed `tools/validate_output.py`
- The `STACKOVERFLOW_API_KEY` env var must be set before any `scrapy crawl` command — the middleware fails fast at startup if absent


## Phase 8: Review remediation (2026-09-28)

T001–T030 record the original implementation pass, not proof of current acceptance. T031–T037 supersede their outdated startup, queue-only resume, pre-write ID commit, custom-filter and retry details. Live stage gates remain separate from these code tasks.

User approved the eight clarified decisions in spec.md. Checklist markers remain reviewer-owned and unchanged; implementation proceeds on this explicit confirmation.

- [X] T031 Add offline regression tests for startup, credentials, exact target, schema/integrity, retries, quota, page cap, and restart.
- [X] T032 Fix Scrapy 2.19 startup and middleware hooks; eliminate request replacement loops; prevent secrets in logs/checkpoints.
- [X] T033 Enforce normalized schema, committed-ID deduplication, exact targets, output integrity, and truthful counters.
- [X] T034 Add sequential page completion and durable safe replay inside JOBDIR; preserve failed/current page on interruption or quota stop.
- [X] T035 Implement exponential retry/backoff and fatal failure status; retain valid items in the final quota response; enforce application cap of 25 pages.
- [X] T036 Harden validator and staged CLI; align quickstart, plan, data model, research and contract with approved decisions.
- [X] T037 Run offline unit/integration tests, record evidence and distinguish them from live stage acceptance.


### Remediation validation evidence

See [validation-report.md](validation-report.md). All T031–T037 code tasks are complete. Live pilot, live small-batch pause/resume, and full-scale dataset acceptance have **not** been performed during remediation. No production output was changed. The environment available to the agent did not contain STACKOVERFLOW_API_KEY; the user's separate terminal may still have it. Reviewer-owned checklist files were left unchanged.


## Phase 9: Numeric analysis dataset

- [X] T038 [US5] Define numeric export requirements and add regression tests for decoding, stable mapping, repeated baskets, schema/duplicate failures and source preservation.
- [X] T039 [US5] Implement offline streaming numeric exporter and reusable tag dictionary in `tools/export_numeric.py`.
- [X] T040 [US5] Document CLI/data model and export the available nonempty dataset; verify every numeric row against the source and record evidence.

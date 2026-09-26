# Feature Specification: Stack Overflow Transaction Data Collector

**Feature Branch**: `001-so-transaction-collector`

**Created**: 2026-09-26

**Status**: Draft

**Input**: User description: "Build a Stack Overflow transaction data collector. A transaction is one Stack Overflow question. The items of a transaction are the tags assigned to that question."

---

## Clarifications

### Session 2026-09-26

- Q: When the Stack Exchange API responds with a rate-limit error or a temporary server error during collection, what should the system do? → A: Retry automatically with exponential back-off; halt and surface a clear error after N consecutive failures (N = configurable, default 5).
- Q: If the output file is found corrupted or truncated on restart, what should the system do? → A: Scan line-by-line, truncate to the last valid JSON record, rebuild the seen question_id set from the healthy portion. Resume from the last known safe crawl checkpoint. If checkpoint-to-output consistency cannot be guaranteed, roll back to the start of the affected page and re-fetch it; deduplication prevents duplicate transactions. Never silently skip corrupted lines.
- Q: When the collector is restarted and the target record count is already met, what should the system do? → A: Count distinct question_ids in the output file at startup; if the distinct count is ≥ 100,000, exit cleanly with a "target already met" message before making any API calls.
- Q: What should the system do when the Stack Exchange API quota is fully exhausted before 100,000 valid transactions have been collected? → A: Stop the current run immediately, preserve the crawl checkpoint and persisted deduplication state, log the current valid transaction count and available quota-reset information, and exit with a clear resumable status. Do not retry while quota is exhausted.
- Q: When the Stack Exchange API returns an empty page of results, what should the system do? → A: Treat the empty page as the end of the current date partition; advance to the next configured partition. Exit cleanly and emit the final summary only when all partitions are exhausted or the 100,000 distinct-ID target is reached. Do not retry a genuinely empty response.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Launch Initial Data Collection (Priority: P1)

A data analyst wants to collect Stack Overflow question-tag data for market basket analysis or association rule mining. They configure credentials, launch the collector, and receive a JSON file of transactions where each transaction contains a question's unique ID and its associated tags.

**Why this priority**: The collector cannot deliver value until it can successfully fetch, validate, and persist at least one batch of valid transactions. This is the foundational flow.

**Independent Test**: Can be fully tested by running the collector for a single date partition, confirming at least one transaction appears in the output file, and verifying the JSON structure is correct.

**Acceptance Scenarios**:

1. **Given** valid credentials are configured and no prior output exists, **When** the collector is launched, **Then** it fetches questions from the Stack Exchange data source, filters out questions with fewer than 2 unique tags, and writes the remaining transactions to a JSON output file in the correct format.
2. **Given** the collector is running, **When** a question has 0 or 1 tags, **Then** that question is silently excluded from the output and counted in the dropped-record log.
3. **Given** credentials are missing or invalid, **When** the collector is launched, **Then** it terminates immediately with a clear, actionable error message before making any data requests.

---

### User Story 2 - Pause and Resume Collection (Priority: P2)

A data analyst starts a long-running collection job, needs to stop it mid-way (e.g., machine restart, quota limit), and later resumes the job from where it left off without re-collecting or duplicating already-saved transactions.

**Why this priority**: Collecting 100,000+ records is a multi-hour operation. Safe interruption and resumption is required for practical use; without it, users cannot reliably complete the target dataset.

**Independent Test**: Can be fully tested by running the collector until ~1,000 transactions are saved, stopping it, restarting, then verifying the final record count has no duplicates (duplicate question_id count = 0) and collection continues from the last page checkpoint.

**Acceptance Scenarios**:

1. **Given** a collection job has processed 5,000 transactions and is interrupted, **When** the job is restarted, **Then** it resumes from the last saved pagination checkpoint and does not re-write any question_id already in the output.
2. **Given** the same question_id arrives again after a restart, **When** it passes through the deduplication stage, **Then** it is silently dropped and does not appear twice in the output.
3. **Given** a job resumes, **When** collection completes, **Then** the total unique question_id count in the output equals the count from before interruption plus new records collected after restart.

---

### User Story 3 - Observe Progress and Validate Output (Priority: P3)

A data analyst monitors a running collection job to understand how many records have been collected, how many have been dropped, and whether pagination is advancing correctly. After the job completes, they validate the final dataset meets quality criteria.

**Why this priority**: Without progress visibility, users cannot distinguish a stuck job from a slow one, nor confirm correctness without manually inspecting the output file.

**Independent Test**: Can be tested by observing live log output during a small run: confirm page numbers advance, record counts increment, and drop counts are reported. After the run, use the validation utility to confirm 0 duplicate question_ids and all records have >= 2 tags.

**Acceptance Scenarios**:

1. **Given** the collector is running, **When** each page of results is processed, **Then** the system logs: current page number, total records fetched so far, total records written so far, and total records dropped (< 2 tags) so far.
2. **Given** the collection job completes, **When** the user inspects the summary report, **Then** it shows: total unique transactions written, total questions dropped, total API pages consumed, and total API quota used.
3. **Given** the output JSON file exists, **When** the user runs the built-in validation check, **Then** it reports whether all question_ids are unique, all records have >= 2 tags, and whether the total record count meets or exceeds 100,000.

---

### User Story 4 - Reach 100,000+ Valid Transactions (Priority: P1)

The system must be capable of accumulating a dataset of at least 100,000 unique, valid transactions without manual intervention beyond the initial launch.

**Why this priority**: This is the primary deliverable. The collector has no purpose if it cannot reach the required scale.

**Independent Test**: Can be tested by running the full collection to completion and asserting the final JSON file contains >= 100,000 distinct question_ids, each with >= 2 tags.

**Acceptance Scenarios**:

1. **Given** the collector runs until the data source is exhausted or the target count is reached, **When** the job ends, **Then** the output file contains >= 100,000 records, each uniquely identified by question_id and each carrying >= 2 tags.
2. **Given** the output dataset is validated post-run, **When** a deduplication check is executed against all question_ids, **Then** the duplicate count is exactly 0.

---

### Edge Cases

- **Empty page (end of date partition)**: An empty API response signals that the current date partition has been fully consumed. The system advances to the next configured date partition without retrying the empty response. When all partitions are exhausted or the 100,000 distinct question_id target is reached, the system stops pagination, emits the final summary report, and exits cleanly.
- **Rate-limit / transient API error**: The system retries the failed request using exponential back-off. After 5 consecutive failures for the same request, the system halts with a clear error message identifying the failed page and the last error code; the crawl checkpoint is preserved so the run can be resumed manually.
- **Corrupted / truncated output file**: On restart, the system scans the output file line-by-line, truncates everything after the last valid JSON record, and rebuilds the seen question_id set from the healthy portion. It then resumes from the last safe crawl checkpoint. If checkpoint-to-output consistency cannot be confirmed, the system rolls back to the start of the affected page and re-fetches it; the deduplication stage prevents any records from being written twice. Corrupted lines are never silently skipped — the recovery action is always logged.
- What happens when a question is returned by the API with duplicate tags (e.g., ["python", "python"])?
- **Target already met on restart**: At startup, the system counts distinct question_ids in the output file. If the count is ≥ 100,000, the system exits cleanly with a "target already met" message before making any API calls.
- **API quota exhausted before target reached**: The system detects quota exhaustion from the API response, stops the current run immediately without further retries, preserves the crawl checkpoint and the persisted deduplication state, logs the current valid transaction count and all available quota-reset information (e.g., reset time), and exits with a clear resumable status message.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST collect Stack Overflow question data exclusively via the official Stack Exchange API (api.stackexchange.com).
- **FR-002**: The system MUST discard any question whose tag list contains fewer than 2 distinct tags; discarded questions must never appear in the output.
- **FR-003**: The system MUST ensure every transaction in the output is uniquely identified by its question_id; no question_id may appear more than once.
- **FR-004**: The system MUST produce a final output file in JSON format, where each record represents one transaction with its question_id and associated tags.
- **FR-005**: The system MUST support safe interruption at any point during collection and must be resumable from the last successfully saved pagination checkpoint.
- **FR-006**: The system MUST NOT re-insert a question_id that already exists in the output when restarted after interruption.
- **FR-007**: The system MUST log progress per paginated batch, including: page number, cumulative records written, and cumulative records dropped.
- **FR-008**: The system MUST produce a summary report at the end of each run including: total transactions written, total dropped, total API pages consumed, and API quota remaining.
- **FR-009**: The system MUST fail fast at startup with a clear error message if required credentials are absent or invalid, before any data requests are made.
- **FR-010**: The system MUST be capable of accumulating at least 100,000 unique, valid transactions across one or more resumed runs.
- **FR-011**: The system MUST deduplicate tag lists per question before applying the >= 2 distinct tag filter (e.g., ["python", "python"] counts as 1 unique tag and must be dropped).
- **FR-012**: The system MUST expose a post-run validation utility that checks the output for duplicate question_ids, records with < 2 tags, and reports the total valid transaction count.
- **FR-013**: On a rate-limit or transient server error, the system MUST retry the failed API request using exponential back-off. After 5 consecutive failures for the same request, the system MUST halt with a descriptive error message (failed page, last HTTP status) while preserving the crawl checkpoint for manual resumption.
- **FR-014**: On restart, if the output file is corrupted or truncated, the system MUST scan it line-by-line to locate the last valid JSON record, truncate all content after that record, and rebuild the seen question_id set from the valid portion. If checkpoint-to-output consistency cannot be confirmed, the system MUST roll back to the start of the affected page and re-fetch it. The recovery action MUST be logged; the system MUST NOT silently skip corrupt lines.
- **FR-015**: At startup, the system MUST count distinct question_ids in the existing output file. If the distinct count is ≥ 100,000, the system MUST exit cleanly with a clear "target already met" message before issuing any API requests.
- **FR-016**: When API quota exhaustion is detected, the system MUST immediately stop the current run without further retries, preserve the crawl checkpoint and persisted deduplication state, log the current valid transaction count and all available quota-reset information, and exit with a clear resumable status. The system MUST NOT continue retrying requests while quota is exhausted.
- **FR-017**: The collection MUST be organised into date partitions (configurable date ranges). An empty API page MUST be treated as the end of the current partition; the system MUST advance to the next configured partition without retrying the empty response. The system MUST stop and emit the final summary report only when all partitions are exhausted or the target of 100,000 distinct valid transactions is reached.

### Key Entities

- **Transaction**: A single Stack Overflow question processed as a market-basket record. Identified by question_id. Valid only when it carries >= 2 distinct tags.
- **Tag**: A string label assigned to a question by the Stack Overflow community. The items within a transaction.
- **Date Partition**: A configured date range (e.g., a calendar month or week) used to segment the full collection into independently pageable slices. The collector exhausts one partition before advancing to the next.
- **Crawl Checkpoint**: A persisted record of the last successfully paginated API offset **within the current date partition**, used to resume interrupted runs from the correct page and partition.
- **Output Dataset**: The accumulated JSON file of valid, deduplicated transactions. The primary deliverable.
- **Validation Report**: A post-run summary confirming dataset integrity (duplicate count, minimum tag count, total record count).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The output dataset contains at least 100,000 records, each with a unique question_id and >= 2 distinct tags — verifiable by running the built-in validation utility.
- **SC-002**: After any interruption and restart, the duplicate question_id count in the output is exactly 0 — verifiable by a set-difference or group-by check on question_id.
- **SC-003**: Progress logs advance monotonically during collection — the cumulative written count never decreases between log lines, confirming forward pagination.
- **SC-004**: The drop count reported in the summary matches the number of questions present in the raw API responses but absent from the output file — verifiable by comparing page-level fetch totals to written totals.
- **SC-005**: The system successfully restarts and continues collection from the correct page checkpoint after interruption — verifiable by confirming no records before the checkpoint boundary are re-written.
- **SC-006**: The collection process completes the full 100,000-record dataset without requiring manual page-management intervention.
- **SC-007**: The startup credential check catches missing or invalid credentials within the first 5 seconds of launch and does not make any data API calls before failing.

---

## Assumptions

- The Stack Exchange API is the sole authorised data source; browser automation and direct HTML scraping are explicitly out of scope per the project constitution.
- An API key will be provided by the user via environment variable; anonymous access quota (~300 requests/day) is insufficient for the 100,000-record target and a registered key is required.
- The collection will proceed in three validated stages (pilot <= 500, small batch <= 5,000, full scale 100,000+) as required by the project constitution; the spec covers all three stages under a single feature.
- The data source is segmented into **date partitions** (configurable date ranges); the collector exhausts one partition before advancing to the next. An empty page from the API is the signal that a partition is complete.
- The output JSON format stores one JSON object per line (JSON Lines / NDJSON) for efficient append-on-resume without loading the entire file into memory.
- Deduplication state (seen question_ids) is maintained across restarts and across partitions using the same persistent output file as the source of truth.
- The Scrapy framework's native job-persistence mechanism (JOBDIR) is the approved checkpoint strategy per the project constitution; the checkpoint tracks both the current partition and the current page within that partition.
- The pipeline execution order (TagValidation -> Normalization -> Deduplication -> Storage at priorities 100/200/300/400) is fixed by the project constitution and is not subject to change within this feature.
- The system is operated by a technically proficient user comfortable with command-line tools; a graphical user interface is out of scope.
- Questions with tags that are semantically equivalent but not string-identical (e.g., "c++" vs "C++") are treated as distinct tags before case normalisation is applied by the normalization pipeline.

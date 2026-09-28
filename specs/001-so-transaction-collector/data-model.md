# Data Model: Stack Overflow Transaction Data Collector

Updated 2026-09-28 following user-approved review clarifications.

## Raw item and persisted transaction

`StackOverFlowItem` is the data-transfer object from the API. It contains no business logic.

| Field | Raw API type | Persisted JSON type | Rule |
|---|---|---|---|
| question_id | integer | integer | Required, positive, bool is not an ID; unique across the output |
| tags | list of strings | list of strings | Required; lowercase and strip, remove empty/duplicate tags, then require ≥2 |
| title | string | string | Required; preserved |
| creation_date | positive UNIX integer | string | Required; UTC `YYYY-MM-DDTHH:MM:SSZ` |
| score | integer | integer | Defaults to 0 if omitted |
| answer_count | integer | integer | Defaults to 0 if omitted; nonnegative |

Output is UTF-8 NDJSON, one complete record per line. `validation.py` defines the shared persisted-record contract for pipeline normalization, startup integrity checking, and the validation CLI.

## Pipeline responsibilities

| Priority | Pipeline | Responsibility |
|---|---|---|
| 100 | TagValidationPipeline | Compute a normalized/deduplicated view of tags and reject malformed tags or fewer than two distinct tags |
| 200 | NormalizationPipeline | Store the normalized tag representation, convert the timestamp, check the complete schema |
| 300 | DeduplicationPipeline | Rebuild committed IDs from output; reserve incoming IDs; reject duplicates and additions beyond target |
| 400 | StoragePipeline | Write, flush and fsync; only then acknowledge the ID to DeduplicationPipeline |

Deduplication owns both the committed-ID set and temporary pending reservations. A storage error releases the reservation, stops the crawl, and leaves the page checkpoint unchanged. Routine drops are DEBUG-only and counted separately as `insufficient_tags`, `duplicate_ids`, and `invalid_schema`.

## Date partition and cursor

Partitions have an inclusive start and exclusive end in UTC, normally seven days; the final partition may be shorter. API `todate` is inclusive, so the request uses exclusive end minus one second. Requests use `sort=creation`, `order=asc`, and `pagesize=100`.

Cursor is `(partition_index, page)`, page 1–25. After an empty page, `has_more=false`, or page 25, advance to the next partition. At page 25 with `has_more=true`, explicitly log skipped remaining questions. This is an application sampling cap, not a claim of exhaustive collection or an API-wide limit.

## Checkpoint and recovery

`JOBDIR` retains Scrapy's disk request queue. `JOBDIR/collector.json` is an atomic, credential-free page checkpoint owned by `PageCheckpoint`, containing configuration, current cursor, output identity/size and a backoff deadline. This supersedes the earlier queue-only design because Scrapy's pending queue alone cannot preserve an in-flight page.

- Write the current cursor before requesting its page.
- Yield items sequentially and await Scrapy's completion/drop/error signal for each item.
- Advance the cursor only after every item on the page is settled. A stop at the target keeps the current page replayable when the target increases.
- After quota reaches zero in a successful response, retain its valid items and save the next cursor before stopping.
- At restart, trust the cursor only if its configuration, shape and output identity/size agree. Otherwise replay from the first configured partition and deduplicate.
- Discard queued requests from earlier process IDs; generate work from the trusted cursor. If Scrapy's queue is unreadable, preserve its files and fall back to an in-memory queue plus the durable cursor.
- Only malformed final JSON/UTF-8 may be truncated. Existing schema errors, duplicate IDs and non-final corrupt lines are fatal. A valid final record lacking a newline is preserved and terminated before append.

Use one process per output/JOBDIR pair; manual edits to healthy output during a crawl are unsupported.

## Counters

`total_written` is the number of committed unique IDs across all runs. `written_this_run`, page/fetch counts and categorized drop counts belong to the current run. Fetched records left unprocessed after an exact-target stop are not classified as dropped; the page is replayable.


## Numeric analysis artifacts

- `transactions_numeric.txt`: one transaction per source question; each row consists solely of ascending distinct positive integer tag IDs separated by spaces, with no header, question ID, or context fields.
- `tag_mapping.json`: a JSON object from normalized tag string to unique positive integer. Initial assignments start at 1 in source encounter order. Existing assignments remain stable and new tags use max(ID)+1.
- Multiple source questions with identical tag sets produce identical output rows; they are not deduplicated by basket because their multiplicity matters for association-rule support.
- The canonical transaction schema and crawler pipelines remain unchanged. Numeric export is a derived artifact; decoding requires its dictionary.

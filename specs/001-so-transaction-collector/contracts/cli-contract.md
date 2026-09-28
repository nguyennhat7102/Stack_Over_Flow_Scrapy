# CLI Contract: Stack Overflow Transaction Data Collector

Updated 2026-09-28. Run from the directory containing `scrapy.cfg`.

## Crawler

```bash
python3 -m scrapy crawl stackoverflow
```

Set `STACKOVERFLOW_API_KEY` in the same terminal. Missing/empty keys fail at startup without API requests. A supplied key is checked with `/info?site=stackoverflow` before collecting `/questions`. This preflight consumes an API request. Invalid keys halt before collection pages; network failures may take longer than five seconds because retry/backoff still applies. API keys are added only at the HTTPS transport boundary, removed before retry/queueing, and redacted from logs.

| Setting | Default | Meaning |
|---|---|---|
| JOBDIR | crawl_jobs/stackoverflow | Scrapy queue and collector.json safe-page cursor |
| OUTPUT_FILE | output/transactions.jsonl | UTF-8 NDJSON source of truth |
| PARTITION_START_DATE | 2008-08-01 | Inclusive UTC date |
| PARTITION_END_DATE | Today UTC | Exclusive UTC date; start must precede end |
| TARGET_RECORDS | 100000 | Positive cumulative limit; never append beyond it |
| DOWNLOAD_DELAY | 1 | Fixed delay in seconds; one concurrent request |
| RETRY_TIMES | 5 | Maximum retries after the initial attempt (six attempts total) |
| RETRY_BACKOFF_BASE | 1 | Retry waits: base × 2^(retry number − 1) |

Each seven-day partition is capped at 25 pages by application policy. A `PAGE CAP` warning announces remaining questions skipped before advancing. API backoff and numeric Retry-After can lengthen the retry delay. Persistent API errors halt; the current page stays replayable.

| Exit code | Meaning |
|---|---|
| 0 | Target reached/already met, date range exhausted, or graceful user shutdown |
| 1 | Startup/configuration/schema/storage/API failure or exhausted retries |
| 2 | Quota exhausted; retained output and checkpoint allow a later resume |

An exit code of 0 does not itself prove the target was reached: always validate the output. Do not run concurrent processes on the same output/JOBDIR pair.

## Logs

```text
page=5 partition=2023-01-01/2023-01-08 fetched=100 written_this_run=500 dropped=0 total_written=500
FINAL SUMMARY | reason=target_reached | partitions_processed=0 | pages_consumed=5 | total_fetched=500 | total_written=500 | written_this_run=500 | total_dropped=0 | dropped_by_reason={...} | quota_remaining=...
```

`total_written` includes previous runs. Other counters are per-run. Drops distinguish insufficient normalized tags, duplicates and invalid schema; ordinary individual drops are DEBUG-only. Partial-page target stops leave unprocessed items outside the drop count. `reset_time` is logged only as supplied by the API (may be unavailable); do not invent a reset timestamp.

## Validator

```bash
python3 tools/validate_output.py --file output/pilot.jsonl --target 500
```

`--file` defaults to `output/transactions.jsonl`; `--target` defaults to 100000 and must be positive. Check complete schema, normalized distinct tags, JSON/UTF-8 validity, duplicate IDs, and minimum valid distinct count. Report all counts. Exit 0 only when every check passes and target is met, otherwise 1 (invalid CLI arguments: argparse exit 2).

## Staged runner

```bash
bash tools/run_stages.sh [--start-stage 1|2|3] [--output-dir PATH]
```

The runner uses `python3` (`PYTHON` may override it), validates earlier-stage files when resuming a later stage, and requires manual confirmation before scaling. It asks the operator to attest that pause/resume was observed before starting 100,000. It does not claim to automate the manual interruption test. Any crawl/validation failure stops progression.


## Numeric exporter

```bash
python3 tools/export_numeric.py --input PATH [--output PATH] [--mapping PATH] [--format txt|jsonl]
```

`--input` is required and must be canonical crawler NDJSON. Default format: `txt`; default output: `<input-stem>.numeric.txt` beside the source. With JSONL format the default extension is `.jsonl`. Default mapping: `tag_mapping.json` beside the destination.

TXT lines contain sorted integer IDs only, e.g. `1 2 5`; JSONL lines are arrays, e.g. `[1, 2, 5]`. The dictionary is tag-to-ID JSON. ID assignments are stable when reusing it. Existing output is rebuilt from the complete source, not appended. Input is unchanged.

Exit 0 means export completed and counts/paths were printed; exit 1 means source/mapping/I/O validation failed; argparse errors exit 2. All validation and source-stability checks precede publication. Mapping replacement happens first; if transaction-file replacement then fails, the dictionary may include extra unused tags but prior codes remain valid. Retrying is safe. Do not run concurrent exporters against the same output/dictionary.

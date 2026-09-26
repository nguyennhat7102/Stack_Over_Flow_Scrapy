# CLI Contract: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Phase**: 1 | **Date**: 2026-09-26

This document defines the command-line interface contract for the two runnable entry points: the Scrapy crawler and the validation utility.

---

## 1. Crawler Entry Point

### Command

```bash
scrapy crawl stackoverflow
```

Must be run from the project root (directory containing `scrapy.cfg`).

### Required Environment Variables

| Variable | Description | Example |
|---|---|---|
| `STACKOVERFLOW_API_KEY` | Registered Stack Exchange API application key | `aBcDeFgHiJkLmNoPqRsT` |

**Fail-fast**: If `STACKOVERFLOW_API_KEY` is absent or empty, the crawler MUST exit within 5 seconds with a non-zero exit code and the message:

```
[ERROR] STACKOVERFLOW_API_KEY environment variable is not set. Obtain a key at https://stackapps.com/ and set it before running.
```

No API requests are made before this check.

### Key Settings (configurable in `settings.py` or via `-s` CLI flag)

| Setting | Default | Description |
|---|---|---|
| `JOBDIR` | `crawl_jobs/stackoverflow` | Scrapy JOBDIR for checkpoint persistence |
| `OUTPUT_FILE` | `output/transactions.jsonl` | NDJSON output path |
| `PARTITION_START_DATE` | `2008-08-01` | Earliest question date (SO launch) |
| `PARTITION_END_DATE` | Today (UTC) | Latest question date |
| `TARGET_RECORDS` | `100000` | Stop when distinct question_ids reaches this count |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | `1` | Max concurrent requests to Stack Exchange API |
| `DOWNLOAD_DELAY` | `1` | Seconds between requests (API rate compliance) |
| `RETRY_TIMES` | `5` | Max retry attempts per request |

### Override Examples

```bash
# Override output file and start date
scrapy crawl stackoverflow -s OUTPUT_FILE=output/custom.jsonl -s PARTITION_START_DATE=2020-01-01

# Resume from existing JOBDIR
scrapy crawl stackoverflow  # JOBDIR is the same; Scrapy auto-resumes
```

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Clean exit: target reached, all partitions exhausted, or target already met at startup |
| 1 | Fatal error: missing credentials, unrecoverable API error (5 consecutive failures), or corrupted output |

### Progress Logging (stdout / Scrapy log)

Per-page log line format (INFO level):
```
[stackoverflow] page=<N> partition=<YYYY-MM-DD>/<YYYY-MM-DD> fetched=<N> written=<N> dropped=<N> total_written=<N>
```

Final summary (INFO level, emitted at spider close):
```
[stackoverflow] FINAL SUMMARY | partitions_processed=<N> | pages_consumed=<N> | total_fetched=<N> | total_written=<N> | total_dropped=<N> | quota_remaining=<N>
```

Quota-exhausted exit (WARNING level):
```
[stackoverflow] QUOTA EXHAUSTED | quota_remaining=0 | reset_time=<ISO-8601> | total_written=<N> | checkpoint_saved=True
```

Target-already-met exit (INFO level):
```
[stackoverflow] TARGET ALREADY MET | distinct_ids=<N> >= target=100000 | no API calls made
```

---

## 2. Validation Utility Entry Point

### Command

```bash
python tools/validate_output.py [--file PATH]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--file` | `output/transactions.jsonl` | Path to NDJSON output file to validate |

### Output (stdout)

```
=== Validation Report: output/transactions.jsonl ===
Total lines          : 112,453
Distinct question_ids: 112,453
Duplicate IDs        : 0
Records with < 2 tags: 0
Target (100,000) met : YES

✅ Dataset is VALID — 112,453 unique transactions, all with ≥ 2 tags.
```

If issues are found:
```
=== Validation Report: output/transactions.jsonl ===
Total lines          : 50,200
Distinct question_ids: 50,198
Duplicate IDs        : 2
Records with < 2 tags: 0
Target (100,000) met : NO

❌ Dataset has issues — see above. Re-run the crawler to complete collection.
```

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | No issues found; target met |
| 1 | Issues found (duplicates, under-tagged records) or target not yet met |

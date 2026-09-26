# Quickstart & Validation Guide: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Phase**: 1 | **Date**: 2026-09-26

This guide covers how to set up, run, and validate the collector at each of the three required stages (pilot, small-batch, full-scale).

---

## Prerequisites

1. Python 3.9+ installed.
2. Project dependencies installed (`pip install scrapy itemadapter`).
3. A registered Stack Exchange API key ([get one at stackapps.com](https://stackapps.com/)).
4. Working directory: **project root** (the directory containing `scrapy.cfg`).

---

## Setup

```bash
# Set your API key (required)
export STACKOVERFLOW_API_KEY="your_key_here"

# Create output directory
mkdir -p output crawl_jobs
```

---

## Stage 1: Pilot Run (≤ 500 questions)

**Goal**: Validate output schema, tag filtering, and deduplication — before any larger run.

```bash
# Crawl a single 7-day partition (2023-01-01 to 2023-01-08) with page limit
scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2023-01-01 \
  -s PARTITION_END_DATE=2023-01-08 \
  -s TARGET_RECORDS=500 \
  -s OUTPUT_FILE=output/pilot.jsonl \
  -s JOBDIR=crawl_jobs/pilot
```

**Expected log lines to verify**:
- `page=1 partition=2023-01-01/2023-01-08 fetched=100 written=<N> dropped=<M>`
- `FINAL SUMMARY | total_written=<N> | total_dropped=<M>`

**Validate**:
```bash
python tools/validate_output.py --file output/pilot.jsonl
```

**Pass criteria**:
- Distinct question_ids = Total lines (0 duplicates)
- Records with < 2 tags = 0
- At least 1 record written

---

## Stage 2: Small-Batch Run (≤ 5,000 questions)

**Goal**: Validate pipeline throughput and pause/resume. Must pass before full-scale run.

```bash
# Start collection
scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2023-01-01 \
  -s TARGET_RECORDS=5000 \
  -s OUTPUT_FILE=output/small_batch.jsonl \
  -s JOBDIR=crawl_jobs/small_batch
```

**Pause test** (interrupt with Ctrl+C after ~1,000 records written, then resume):
```bash
# Resume — Scrapy reads JOBDIR automatically
scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2023-01-01 \
  -s TARGET_RECORDS=5000 \
  -s OUTPUT_FILE=output/small_batch.jsonl \
  -s JOBDIR=crawl_jobs/small_batch
```

**Validate**:
```bash
python tools/validate_output.py --file output/small_batch.jsonl
```

**Pass criteria**:
- Distinct question_ids = Total lines (0 duplicates — critical: proves resume did not re-insert)
- Records with < 2 tags = 0
- Total written ≥ 1,000 and ≤ 5,000

---

## Stage 3: Full-Scale Run (≥ 100,000 questions)

**Goal**: Collect the complete target dataset. Only permitted after Stages 1 and 2 pass.

```bash
scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2008-08-01 \
  -s TARGET_RECORDS=100000 \
  -s OUTPUT_FILE=output/transactions.jsonl \
  -s JOBDIR=crawl_jobs/stackoverflow
```

This run may take multiple sessions (quota exhaustion is expected). Each time it stops, simply re-run the same command — the JOBDIR and output file persist state between runs.

**Monitor progress** (tail the Scrapy log):
```
[stackoverflow] page=3 partition=2008-09-01/2008-09-08 fetched=300 written=287 dropped=13 total_written=1243
```

**Final validation** (run once the crawler exits with "target reached"):
```bash
python tools/validate_output.py --file output/transactions.jsonl
```

**Pass criteria**:
- Distinct question_ids ≥ 100,000
- Duplicate IDs = 0
- Records with < 2 tags = 0

---

## Acceptance Test: Pause / Resume Deduplication Proof

Run this sequence to formally verify SC-002 (no duplicates after restart):

```bash
# 1. Start fresh
rm -rf output/test.jsonl crawl_jobs/test
scrapy crawl stackoverflow \
  -s TARGET_RECORDS=2000 \
  -s OUTPUT_FILE=output/test.jsonl \
  -s JOBDIR=crawl_jobs/test &

# 2. Wait until ~1000 lines, then interrupt
sleep 30 && kill %1

# 3. Record count before resume
wc -l output/test.jsonl

# 4. Resume
scrapy crawl stackoverflow \
  -s TARGET_RECORDS=2000 \
  -s OUTPUT_FILE=output/test.jsonl \
  -s JOBDIR=crawl_jobs/test

# 5. Validate — duplicate count MUST be 0
python tools/validate_output.py --file output/test.jsonl
```

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---|---|---|
| `STACKOVERFLOW_API_KEY is not set` at startup | Missing env var | `export STACKOVERFLOW_API_KEY=...` |
| `QUOTA EXHAUSTED` in log | Daily API quota used up | Wait for quota reset (shown in log), then re-run same command |
| `TARGET ALREADY MET` at startup | Output file already has ≥ 100k distinct IDs | Collection complete — run validation utility to confirm |
| Validation reports duplicate IDs | Output file was manually edited or two runs used different JOBDIR with same output file | Inspect with `jq` and reconcile; ensure each run uses a consistent JOBDIR + output file pair |
| `FINAL SUMMARY` shows dropped >> written | Unusual for SO data; may indicate wrong date partition (very old/inactive period) | Try a more recent `PARTITION_START_DATE` |

---

## References

- [Data Model](data-model.md) — Entity definitions, field types, pipeline rules
- [CLI Contract](contracts/cli-contract.md) — Full CLI options, log formats, exit codes
- [Feature Spec](spec.md) — Functional requirements and success criteria

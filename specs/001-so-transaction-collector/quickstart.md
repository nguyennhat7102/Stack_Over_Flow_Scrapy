# Quickstart & Validation Guide: Stack Overflow Transaction Data Collector

**Branch**: `001-so-transaction-collector` | **Phase**: 1 | **Date**: 2026-09-26

This guide covers how to set up, run, and validate the collector at each of the three required stages (pilot, small-batch, full-scale).

---

## Prerequisites

1. Python 3.10+ installed.
2. Project dependencies installed (`python3 -m pip install -r requirements.txt`).
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

## Automated Stage Runner (recommended)

Use `tools/run_stages.sh` to run all three stages automatically with validation gates:

```bash
bash tools/run_stages.sh
```

The script runs Stage 1 → Stage 2 → Stage 3 in sequence, calls
`python3 tools/validate_output.py --target <N>` after each stage, and requires
manual confirmation before advancing. It aborts on any crawl or validation failure.
The pause/resume test is manual; before full scale the operator must confirm it was observed.
Starting at a later stage still validates the preceding stage files.

To resume from a specific stage:

```bash
bash tools/run_stages.sh --start-stage 2
```

---

## Stage 1: Pilot Run (≤ 500 questions)

**Goal**: Validate output schema, tag filtering, and deduplication — before any larger run.

```bash
# Crawl a single 7-day partition (2023-01-01 to 2023-01-08) with page limit
python3 -m scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2023-01-01 \
  -s PARTITION_END_DATE=2023-01-08 \
  -s TARGET_RECORDS=500 \
  -s OUTPUT_FILE=output/pilot.jsonl \
  -s JOBDIR=crawl_jobs/pilot
```

**Expected log lines to verify**:

- `page=1 partition=2023-01-01/2023-01-08 fetched=100 written_this_run=<N> dropped=<M>`
- `FINAL SUMMARY | total_written_this_run=<N> | total_dropped=<M>`

**Validate**:

```bash
python3 tools/validate_output.py --file output/pilot.jsonl --target 500
```

**Pass criteria**:

- Distinct question_ids = Total lines (0 duplicates)
- Records with < 2 tags = 0
- Exactly 500 valid records written

---

## Stage 2: Small-Batch Run (≤ 5,000 questions)

**Goal**: Validate pipeline throughput and pause/resume. Must pass before full-scale run.

```bash
# Start collection
python3 -m scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2023-01-01 \
  -s TARGET_RECORDS=5000 \
  -s OUTPUT_FILE=output/small_batch.jsonl \
  -s JOBDIR=crawl_jobs/small_batch
```

**Pause test** (interrupt with Ctrl+C after ~1,000 records written, then resume):

```bash
# Resume — Scrapy reads JOBDIR automatically
python3 -m scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2023-01-01 \
  -s TARGET_RECORDS=5000 \
  -s OUTPUT_FILE=output/small_batch.jsonl \
  -s JOBDIR=crawl_jobs/small_batch
```

**Validate**:

```bash
python3 tools/validate_output.py --file output/small_batch.jsonl --target 5000
```

**Pass criteria**:

- Distinct question_ids = Total lines (0 duplicates — critical: proves resume did not re-insert)
- Records with < 2 tags = 0
- Exactly 5,000 valid records written; an interruption and resume were observed

---

## Stage 3: Full-Scale Run (≥ 100,000 questions)

**Goal**: Collect the complete target dataset. Only permitted after Stages 1 and 2 pass.

```bash
python3 -m scrapy crawl stackoverflow \
  -s PARTITION_START_DATE=2008-08-01 \
  -s TARGET_RECORDS=100000 \
  -s OUTPUT_FILE=output/transactions.jsonl \
  -s JOBDIR=crawl_jobs/stackoverflow
```

This run may take multiple sessions (quota exhaustion is expected). Each time it stops, simply re-run the same command — the JOBDIR and output file persist state between runs.

**Monitor progress** (tail the Scrapy log):

```
[stackoverflow] page=3 partition=2008-09-01/2008-09-08 fetched=100 written_this_run=287 dropped=13 total_written=1243
```

**Final validation** (run once the crawler exits with "target reached"):

```bash
python3 tools/validate_output.py --file output/transactions.jsonl
```

**Pass criteria**:

- Distinct question_ids ≥ 100,000
- Duplicate IDs = 0
- Records with < 2 tags = 0

---

## Acceptance Test: Pause / Resume Deduplication Proof

Use the Stage 2 command above. While it runs, use a second terminal to check:

```bash
wc -l output/small_batch.jsonl
```

After approximately 1,000 lines, press Ctrl+C once in the crawler terminal and wait for shutdown. Record the line count. Re-run the identical Stage 2 command in that terminal. When it reaches 5,000:

```bash
python3 tools/validate_output.py --file output/small_batch.jsonl --target 5000
```

Expected: exactly 5,000 valid distinct IDs, no duplicates, no schema errors. The resumed log must show the prior count loaded and a restored page checkpoint. A partially processed page may be fetched again, but its persisted records must not be written twice. Do not delete the output or JOBDIR when resuming.

## Recovery and limits

- One process per output/JOBDIR pair. Keep both paths and the date range consistent across runs.
- At most 25 pages per seven-day partition. If more remain, a `PAGE CAP` warning is logged and collection moves to the next partition. This is intentional sampling, not an exhaustive export.
- Exit 2 means quota exhaustion; exit 1 means a failure requiring inspection. Re-run the same command once resolved.
- A malformed final line is removed on startup. Invalid existing schema or corrupt middle lines stop startup; inspect them instead of blindly deleting output.
- `total_written` includes previous sessions; `written_this_run` counts only the current session.
- If a single pilot week yields fewer than 500 valid records, validation fails. Extend the end date and repeat; the changed range safely replays with deduplication.

## Offline regression tests

```bash
python3 -m unittest discover -s tests -v
```

These tests use temporary files and a synthetic API transport; they do not replace live pilot/small-batch acceptance.

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
| --- | --- | --- |
| `STACKOVERFLOW_API_KEY is not set` at startup | Missing env var | `export STACKOVERFLOW_API_KEY=...` |
| `QUOTA EXHAUSTED` in log | Daily API quota used up | Wait until quota is available again, then re-run the same command; reset time may not be supplied |
| `TARGET ALREADY MET` at startup | Output file already meets TARGET_RECORDS | Collection complete — run validation utility to confirm |
| Validation reports duplicate IDs | Output file was manually edited or two runs used different JOBDIR with same output file | Inspect with `jq` and reconcile; ensure each run uses a consistent JOBDIR + output file pair |
| `FINAL SUMMARY` shows dropped >> written | Unusual for SO data; may indicate wrong date partition (very old/inactive period) | Try a more recent `PARTITION_START_DATE` |

---

## References

- [Data Model](data-model.md) — Entity definitions, field types, pipeline rules
- [CLI Contract](contracts/cli-contract.md) — Full CLI options, log formats, exit codes
- [Feature Spec](spec.md) — Functional requirements and success criteria


## Export numeric transactions (TXT)

After stopping the crawler, export the completed source file:

```bash
python3 tools/export_numeric.py \
  --input output/pilot.jsonl \
  --output output/transactions_numeric.txt \
  --mapping output/tag_mapping.json
```

Use the actual populated crawl file as `--input` (it need not be named pilot.jsonl). Each output line is a transaction such as `1 2 5`, containing only ascending distinct positive integer tag codes. The output does not include question IDs or other metadata. A separate JSON dictionary maps tag names to IDs, for example `{"python": 1, "java": 2}`; actual IDs depend on the source's tag encounter order.

Keep the dictionary with the TXT file. Re-running with the same dictionary preserves all assigned IDs and appends codes for unseen tags. Different questions with the same tag set remain separate identical TXT lines to preserve support/frequency. Re-running rebuilds the TXT file, not appends it, so reruns do not multiply transactions.

Default format is TXT. If no `--output` is given, the result is `<input-stem>.numeric.txt` next to the input. Optional `--format jsonl` emits arrays instead. Export is offline and requires no API key. The canonical JSONL input is never modified and remains necessary for crawler deduplication/resume.

Do not export while the crawler is changing the source. Empty/invalid input, duplicate question IDs, invalid dictionaries and source/output path collisions cause a nonzero exit. If the source changes during the operation, export aborts; run again after the crawl stops. Always reuse the matching dictionary for subsequent exports.

#!/usr/bin/env bash
# Usage: bash tools/run_stages.sh [--start-stage 1|2|3] [--output-dir PATH]
set -euo pipefail
START_STAGE=1
BASE_OUTPUT_DIR=output
JOBDIR_BASE=crawl_jobs
PYTHON="${PYTHON:-python3}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-stage|--output-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for $1"; exit 1; }
      if [[ "$1" == --start-stage ]]; then START_STAGE="$2"; else BASE_OUTPUT_DIR="$2"; fi
      shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done
[[ "$START_STAGE" =~ ^[123]$ ]] || { echo 'Stage must be 1, 2, or 3'; exit 1; }
[[ -n "${STACKOVERFLOW_API_KEY:-}" ]] || { echo 'STACKOVERFLOW_API_KEY is not set'; exit 1; }

confirm() {
  local answer
  read -rp "$1 [y/N] " answer || { echo 'Confirmation required'; exit 1; }
  [[ "$answer" =~ ^[Yy]$ ]] || { echo 'Aborted'; exit 0; }
}
validate_stage() {
  "$PYTHON" tools/validate_output.py --file "$1" --target "$2"
}
run_stage() {
  local target="$1" output="$2" jobdir="$3" start="$4" end="$5"
  echo "Collecting ${target} records into ${output}"
  local code=0
  "$PYTHON" -m scrapy crawl stackoverflow \
    -s TARGET_RECORDS="$target" -s OUTPUT_FILE="$output" -s JOBDIR="$jobdir" \
    -s PARTITION_START_DATE="$start" -s PARTITION_END_DATE="$end" || code=$?
  if [[ "$code" -ne 0 ]]; then
    echo "Crawl stopped (exit $code). Resolve the reported condition, then resume this stage."
    exit "$code"
  fi
  validate_stage "$output" "$target"
}

if [[ "$START_STAGE" -eq 1 ]]; then
  run_stage 500 "$BASE_OUTPUT_DIR/stage1_pilot.jsonl" "$JOBDIR_BASE/stage1" 2023-01-01 2023-01-08
else
  validate_stage "$BASE_OUTPUT_DIR/stage1_pilot.jsonl" 500
fi
if [[ "$START_STAGE" -le 2 ]]; then
  confirm 'Pilot validated. Proceed to 5,000? During this run, test Ctrl+C and resume using --start-stage 2.'
  run_stage 5000 "$BASE_OUTPUT_DIR/stage2_small_batch.jsonl" "$JOBDIR_BASE/stage2" 2023-01-01 ''
else
  validate_stage "$BASE_OUTPUT_DIR/stage2_small_batch.jsonl" 5000
fi
confirm 'Both datasets validated. Have you observed a successful pause/resume test and approved the 100,000-record run?'
run_stage 100000 "$BASE_OUTPUT_DIR/transactions.jsonl" "$JOBDIR_BASE/stackoverflow" 2008-08-01 ''
echo 'All stages complete.'

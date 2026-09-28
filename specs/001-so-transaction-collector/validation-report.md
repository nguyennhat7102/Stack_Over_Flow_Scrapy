# Remediation validation — 2026-09-28

## Scope and decisions

Implemented the eight user-confirmed decisions recorded in spec.md, including the explicit choice to stop at page 25 and advance to the next weekly partition without subdivision. Followed speckit-implement with prerequisites, spec/plan/tasks review, regression tests before fixes, implementation, and validation. The user's confirmation authorized proceeding while reviewer-owned checklist markers remained unchanged.

## Environment

- Python 3.14.7
- Scrapy 2.19.0 (package requires Python >=3.10)
- itemadapter pinned to 0.13.1 in requirements.txt
- No STACKOVERFLOW_API_KEY in the agent's process environment

## Evidence

`python3 -m unittest discover -s tests -v`: **23 tests passed** in 13.358 seconds.

The first regression run failed on the original startup, middleware, credential guard, schema and pipeline behavior. After remediation, the full suite covers:

- Real Scrapy engine with a synthetic transport: exactly 500 persisted records; no further API requests on an already-complete restart; increasing target to 550 replays page 5 safely, then adds 50 records from page 6.
- Quota-zero successful response retains its 100 records; later run resumes at page 2 and reaches 500.
- Initial request plus five retries on transient errors, exponential delays, fatal exit code, and safe replay after failure.
- API backoff honored before the next request.
- Cap at page 25, logged skipped remainder, then next week; no request for page 26.
- SIGINT graceful stop/resume and SIGKILL in-flight interruption recovery; final 500 records have 500 distinct IDs.
- Corrupt collector cursor/native queue fallback; invalid output schema stops before API calls.
- Missing credentials and invalid supplied-key preflight stop before question collection.
- Invalid date range exits nonzero; empty partition completes without retry.
- Mixed tag/schema/duplicate drops counted separately while still reaching the exact target.
- Malformed trailing line repair, fatal middle corruption, valid final JSON without newline, strict schema rejection.
- Storage failure does not commit an ID; normalized tags and exclusive date partition boundaries are preserved.
- Integration logs contain no dummy credential and no ScrapyDeprecationWarning; persisted JOBDIR files contain no dummy credential.

Additional checks:

- `bash -n tools/run_stages.sh`: passed.
- Stage runner rejects stage 0, missing option values, and `--start-stage 3` when earlier-stage outputs are absent.
- `git diff --check`: passed.
- Spec Kit prerequisites: passed with feature directory `specs/001-so-transaction-collector` and required tasks available.
- `.specify/extensions.yml` absent: no pre/post implementation hooks registered.

## Live acceptance status

No live Stack Exchange API crawl was executed. Tests use isolated temporary output/JOBDIR paths and a synthetic key. Existing user output and job files were not edited. Offline regression is not evidence that live pilot/small-batch/full-scale gates passed.

Next action: run the pilot command in quickstart.md from the user's terminal with the API key exported; validate with `--target 500`. Do not scale until live pilot and small-batch pause/resume criteria are met.

## Operational limits

- Sampling is intentionally capped at 25 pages per weekly partition, so the dataset is not an exhaustive date-range export.
- One process per output/JOBDIR pair; manual output changes during a crawl are unsupported.
- A partially processed page may be re-fetched after restart; persisted IDs are never intentionally updated.
- A changed date configuration, missing checkpoint, replaced/truncated output, or corrupt checkpoint causes conservative replay. Replaying consumes additional API requests.
- API backoff can outlast a process; its deadline is saved in the checkpoint. An unavailable quota-reset timestamp is not fabricated.


## Numeric export verification (2026-09-28)

The user selected TXT lines containing numeric item codes. Added the offline exporter without changing crawler source files or pipeline behavior.

- `python3 -m unittest tests.test_export_numeric -v`: 8 tests passed, covering full tag roundtrip, repeated baskets, deterministic reruns, stable dictionary extension, TXT syntax, malformed/empty/duplicate input, invalid dictionaries, alias/hardlink protection, changing source detection and partial publication failure.
- Exported the then-current `output/pilot.jsonl` into `output/transactions_numeric.txt` and `output/tag_mapping.json`.
- Result: **22,439 transaction rows**, **9,968 distinct tag IDs**, TXT size **298,311 bytes**.
- Independently decoded and compared every exported row with its source tag set; checked row counts, sorted positive IDs, uniqueness within each row and minimum two tags.
- Source files were not rewritten. `output/transactions.jsonl` was empty, so the populated pilot file was deliberately selected.
- The historic remediation live-acceptance notes above refer to that earlier phase; this export used the user's subsequently collected real local dataset.

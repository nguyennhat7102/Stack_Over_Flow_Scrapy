#!/usr/bin/env python3
"""Validate the persisted transaction contract without contacting the API."""
import argparse
import json
import sys
from pathlib import Path

# Also support `python tools/validate_output.py` from the project root.
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from StackOverFlow.validation import normalized_tags, validate_record


def validate(filepath, target):
    lines = duplicates = under_tagged = parse_errors = schema_errors = 0
    seen = set()
    valid_ids = set()
    read_error = False
    try:
        with open(filepath,'rb') as fh:
            for raw in fh:
                lines += 1
                try:
                    record = json.loads(raw.decode('utf-8'))
                except (ValueError,UnicodeDecodeError):
                    parse_errors += 1
                    continue
                qid = record.get('question_id') if isinstance(record,dict) else None
                if type(qid) is int and qid > 0:
                    if qid in seen: duplicates += 1
                    seen.add(qid)
                try:
                    tags = normalized_tags(record.get('tags') if isinstance(record,dict) else None)
                    if len(tags) < 2: under_tagged += 1
                except ValueError:
                    under_tagged += 1
                try:
                    validate_record(record)
                except ValueError:
                    schema_errors += 1
                    continue
                valid_ids.add(qid)
    except OSError as exc:
        read_error = True
        print(f'Cannot read output: {type(exc).__name__}',file=sys.stderr)
    met = target > 0 and len(valid_ids) >= target
    good = met and not any((duplicates,under_tagged,parse_errors,schema_errors,read_error))
    print(f'\n=== Validation Report: {filepath} ===')
    for key,value in [('Total lines',lines),('Distinct question_ids',len(seen)),
        ('Valid distinct IDs',len(valid_ids)),('Duplicate IDs',duplicates),
        ('Records with < 2 distinct tags',under_tagged),('JSON/UTF-8 parse errors',parse_errors),
        ('Schema errors',schema_errors)]:
        print(f'  {key}: {value:,}')
    print(f'  Target ({target:,}) met: {"YES" if met else "NO"}')
    print('Dataset is VALID' if good else 'Dataset validation FAILED')
    return 0 if good else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',default='output/transactions.jsonl')
    parser.add_argument('--target',type=int,default=100000)
    args=parser.parse_args()
    if args.target <= 0: parser.error('--target must be positive')
    return validate(args.file,args.target)


if __name__=='__main__': sys.exit(main())

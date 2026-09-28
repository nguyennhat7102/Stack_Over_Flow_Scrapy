#!/usr/bin/env python3
"""Export validated crawl records as numeric baskets, with a stable tag dictionary."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from StackOverFlow.validation import validate_record


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate dictionary key: {key}')
        result[key] = value
    return result


def _load_mapping(path):
    if not path.exists():
        return {}
    try:
        mapping = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'Invalid mapping file: {path}: {exc}') from None
    if not isinstance(mapping, dict):
        raise ValueError('Mapping must be a JSON object of tag names to integer IDs')
    used = set()
    for tag, code in mapping.items():
        if not tag or tag != tag.strip().lower():
            raise ValueError('Mapping tags must be nonempty normalized strings')
        if type(code) is not int or code <= 0 or code in used:
            raise ValueError('Mapping IDs must be unique positive integers')
        used.add(code)
    return mapping


def _distinct_paths(paths):
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            same_file = first.exists() and second.exists() and os.path.samefile(first, second)
            if first.resolve() == second.resolve() or same_file:
                raise ValueError('Input, transaction output, and mapping must be different files')


def _fingerprint(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def export_numeric(source, output, mapping_path, *, format='txt'):
    """Stream a stable source, stage both artifacts, then publish mapping before baskets.

    Reused mappings are extended without reassigning existing IDs. Thus even if
    publishing baskets fails after publishing the mapping, old baskets still decode.
    No source/output file is replaced until every input record has been validated.
    """
    if format not in ('txt', 'jsonl'):
        raise ValueError('Format must be txt or jsonl')
    source, output, mapping_path = map(Path, (source, output, mapping_path))
    _distinct_paths([source, output, mapping_path])
    mapping = _load_mapping(mapping_path)
    next_code = max(mapping.values(), default=0) + 1
    output.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = []
    seen_questions = set()
    count = 0
    try:
        with source.open('rb') as incoming, tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n', dir=output.parent,
            prefix='.numeric-', suffix='.tmp', delete=False,
        ) as outgoing:
            temporary.append(Path(outgoing.name))
            initial = _fingerprint(os.fstat(incoming.fileno()))
            for lineno, raw in enumerate(incoming, 1):
                try:
                    record = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
                    validate_record(record)
                except (ValueError, UnicodeError) as exc:
                    raise ValueError(f'Invalid input at line {lineno}: {exc}') from None
                qid = record['question_id']
                if qid in seen_questions:
                    raise ValueError(f'Duplicate question_id at input line {lineno}')
                seen_questions.add(qid)
                for tag in record['tags']:
                    if tag not in mapping:
                        mapping[tag] = next_code
                        next_code += 1
                basket = sorted(mapping[tag] for tag in record['tags'])
                line = ' '.join(map(str, basket)) if format == 'txt' else json.dumps(basket)
                outgoing.write(line + '\n')
                count += 1
            if count == 0:
                raise ValueError('Input is empty; there are no transactions to export')
            if initial != _fingerprint(os.fstat(incoming.fileno())) or initial != _fingerprint(source.stat()):
                raise ValueError('Input changed during export; stop the crawl and run export again')
            outgoing.flush()
            os.fsync(outgoing.fileno())

        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n', dir=mapping_path.parent,
            prefix='.tag-mapping-', suffix='.tmp', delete=False,
        ) as dictionary:
            temporary.append(Path(dictionary.name))
            json.dump(mapping, dictionary, ensure_ascii=False, indent=2)
            dictionary.write('\n')
            dictionary.flush()
            os.fsync(dictionary.fileno())
        # Check again just before publishing; export is intended for a stopped crawl.
        if initial != _fingerprint(source.stat()):
            raise ValueError('Input changed during export; stop the crawl and run export again')
        os.replace(temporary[1], mapping_path)
        os.replace(temporary[0], output)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return {'transactions': count, 'mapped_tags': len(mapping),
            'output': str(output), 'mapping': str(mapping_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Canonical crawler JSONL input')
    parser.add_argument('--output', type=Path, help='Default: <input-stem>.numeric.txt next to input')
    parser.add_argument('--mapping', type=Path, help='Existing/new tag dictionary; default: tag_mapping.json beside output')
    parser.add_argument('--format', choices=('txt', 'jsonl'), default='txt')
    args = parser.parse_args()
    output = args.output or args.input.with_name(f'{args.input.stem}.numeric.{args.format}')
    mapping = args.mapping or output.parent / 'tag_mapping.json'
    try:
        result = export_numeric(args.input, output, mapping, format=args.format)
    except (OSError, ValueError) as exc:
        print(f'Export failed: {exc}', file=sys.stderr)
        return 1
    print(f"Exported {result['transactions']:,} transactions; {result['mapped_tags']:,} mapped tags")
    print(f'Transactions: {output}')
    print(f'Tag mapping: {mapping}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

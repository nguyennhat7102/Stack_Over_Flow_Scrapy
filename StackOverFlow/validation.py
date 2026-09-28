"""Shared transaction contract for pipelines, recovery, and offline validation."""
from datetime import datetime


def normalized_tags(tags):
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ValueError('tags must be a list of strings')
    return list(dict.fromkeys(t.strip().lower() for t in tags if t.strip()))


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError('record must be an object')
    if type(record.get('question_id')) is not int or record['question_id'] <= 0:
        raise ValueError('question_id must be a positive integer')
    tags = record.get('tags')
    clean = normalized_tags(tags)
    if len(clean) < 2 or tags != clean:
        raise ValueError('tags must contain at least two distinct normalized strings')
    if not isinstance(record.get('title'), str):
        raise ValueError('title must be a string')
    for key in ('score', 'answer_count'):
        if type(record.get(key)) is not int:
            raise ValueError(f'{key} must be an integer')
    if record['answer_count'] < 0:
        raise ValueError('answer_count must be nonnegative')
    timestamp = record.get('creation_date')
    if not isinstance(timestamp, str):
        raise ValueError('creation_date must be a UTC timestamp')
    try:
        parsed = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise ValueError('creation_date must use YYYY-MM-DDTHH:MM:SSZ') from None
    if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != timestamp:
        raise ValueError('creation_date must use YYYY-MM-DDTHH:MM:SSZ')

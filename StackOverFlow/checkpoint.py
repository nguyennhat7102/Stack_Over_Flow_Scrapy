"""Atomic, credential-free page checkpoint within the Scrapy JOBDIR."""
import json
import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class PageCheckpoint:
    def __init__(self, settings, partitions):
        jobdir = settings.get('JOBDIR')
        self.path = Path(jobdir)/'collector.json' if jobdir else None
        self.output = Path(settings.get('OUTPUT_FILE', 'output/transactions.jsonl')).resolve()
        self.partitions = partitions
        self.config = {'output': str(self.output), 'partitions': [[a.isoformat(), b.isoformat()] for a,b in partitions]}
        self.cursor = (0, 1)
        self.not_before = 0
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                if saved['config'] != self.config:
                    raise ValueError('configuration changed')
                stat = self.output.stat()
                if list((stat.st_dev, stat.st_ino)) != saved['output_identity'] or stat.st_size < saved['output_size']:
                    raise ValueError('output replaced or truncated')
                cursor = saved['cursor']
                if cursor is not None and (not isinstance(cursor,list) or len(cursor)!=2 or
                    type(cursor[0]) is not int or type(cursor[1]) is not int or
                    not 0 <= cursor[0] < len(partitions) or not 1 <= cursor[1] <= 25):
                    raise ValueError('invalid cursor')
                self.cursor = tuple(cursor) if cursor is not None else None
                self.not_before = float(saved.get('not_before', 0))
                if not math.isfinite(self.not_before):
                    raise ValueError('invalid backoff deadline')
                logger.info('Checkpoint restored: %s', self.cursor)
            except (OSError, ValueError, TypeError, KeyError):
                self.cursor = (0, 1)
                self.not_before = 0
                logger.warning('Untrusted checkpoint; restarting from first partition with deduplication')
        else:
            logger.warning('No trusted checkpoint; starting from first partition with deduplication')
        if not partitions:
            self.cursor = None

    def save(self, cursor):
        self.cursor = cursor
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stat = self.output.stat()
        payload = dict(config=self.config, cursor=cursor, output_size=stat.st_size,
                       output_identity=[stat.st_dev,stat.st_ino], not_before=self.not_before)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as fh:
            json.dump(payload,fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, self.path)

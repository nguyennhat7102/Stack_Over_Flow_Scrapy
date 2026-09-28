"""Validation → normalization → deduplication → durable NDJSON storage."""
import json
import logging
import os
from datetime import datetime, timezone

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from StackOverFlow.validation import normalized_tags, validate_record

logger = logging.getLogger(__name__)


def _load_seen_ids(output_path):
    """Stream the file; only malformed final JSON/UTF-8 may be truncated."""
    seen = set()
    safe_end = 0
    if not os.path.exists(output_path):
        return seen, safe_end
    with open(output_path, 'rb') as fh:
        raw = fh.readline()
        lineno = 0
        while raw:
            lineno += 1
            next_raw = fh.readline()
            try:
                record = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if next_raw:
                    raise RuntimeError(f'Mid-file corruption: {output_path}, line {lineno}') from None
                logger.warning('Removing malformed final line %d from %s', lineno, output_path)
                break
            try:
                validate_record(record)
            except ValueError as exc:
                raise RuntimeError(f'Invalid schema: {output_path}, line {lineno}: {exc}') from None
            qid = record['question_id']
            if qid in seen:
                raise RuntimeError(f'Duplicate question_id in {output_path}, line {lineno}')
            seen.add(qid)
            safe_end += len(raw)
            raw = next_raw
    return seen, safe_end


class CrawlerPipeline:
    @classmethod
    def from_crawler(cls, crawler):
        obj = cls()
        obj.crawler = crawler
        return obj

    def drop(self, category):
        self.crawler.stats.inc_value(f'dropped/{category}')
        raise DropItem(category, log_level=logging.DEBUG)


class TagValidationPipeline(CrawlerPipeline):
    """Validate the normalized tag view while preserving pipeline order."""
    def process_item(self, item):
        try:
            tags = normalized_tags(ItemAdapter(item).get('tags'))
        except ValueError:
            self.drop('invalid_schema')
        if len(tags) < 2:
            self.drop('insufficient_tags')
        return item


class NormalizationPipeline(CrawlerPipeline):
    """Store normalized tags and convert the required timestamp to UTC."""
    def process_item(self, item):
        adapter = ItemAdapter(item)
        try:
            adapter['tags'] = normalized_tags(adapter.get('tags'))
            ts = adapter.get('creation_date')
            if type(ts) is not int or ts <= 0:
                raise ValueError('invalid creation_date')
            adapter['creation_date'] = datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            validate_record(dict(adapter))
        except (ValueError, OverflowError, OSError):
            self.drop('invalid_schema')
        return item


class DeduplicationPipeline(CrawlerPipeline):
    """Own committed IDs; reserve incoming IDs until storage acknowledges them."""
    def open_spider(self):
        settings = self.crawler.settings
        self.target = settings.getint('TARGET_RECORDS', 100000)
        if self.target <= 0:
            raise ValueError('TARGET_RECORDS must be positive')
        self.output_path = settings.get('OUTPUT_FILE', 'output/transactions.jsonl')
        self.seen_ids, self.safe_end_offset = _load_seen_ids(self.output_path)
        self.pending = set()
        self.crawler.collector_dedup = self
        spider = self.crawler.spider
        spider.total_written = len(self.seen_ids)
        logger.info('Loaded %d seen question_ids from %s', len(self.seen_ids), self.output_path)
        if len(self.seen_ids) >= self.target:
            spider.stop_reason = 'target_already_met'
            logger.info('TARGET ALREADY MET | distinct_ids=%d | target=%d | no API calls made', len(self.seen_ids), self.target)

    def process_item(self, item):
        qid = ItemAdapter(item)['question_id']
        if qid in self.seen_ids or qid in self.pending:
            self.drop('duplicate_ids')
        if len(self.seen_ids) + len(self.pending) >= self.target:
            raise DropItem('target_limit', log_level=logging.DEBUG)
        self.pending.add(qid)
        return item

    def commit(self, qid):
        self.pending.discard(qid)
        self.seen_ids.add(qid)
        spider = self.crawler.spider
        spider.total_written = len(self.seen_ids)
        spider.written_this_run += 1
        if len(self.seen_ids) >= self.target:
            spider.stop_reason = 'target_reached'
            logger.info('TARGET REACHED | distinct_ids=%d', len(self.seen_ids))


class StoragePipeline(CrawlerPipeline):
    """Flush and fsync each record before acknowledging it to deduplication."""
    def open_spider(self):
        self._fh = None
        self.dedup = self.crawler.collector_dedup
        self.output_path = self.dedup.output_path
        os.makedirs(os.path.dirname(self.output_path) or '.', exist_ok=True)
        if os.path.exists(self.output_path):
            with open(self.output_path, 'r+b') as fh:
                fh.truncate(self.dedup.safe_end_offset)
                if self.dedup.safe_end_offset:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b'\n':
                        fh.seek(0, os.SEEK_END)
                        fh.write(b'\n')
                fh.flush()
                os.fsync(fh.fileno())
        self._fh = open(self.output_path, 'ab')

    def process_item(self, item):
        record = dict(ItemAdapter(item))
        qid = record['question_id']
        try:
            self._fh.write((json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except (OSError, ValueError):
            self.dedup.pending.discard(qid)
            self.crawler.spider.stop_reason = 'storage_error'
            raise
        self.dedup.commit(qid)
        return item

    def close_spider(self):
        if getattr(self, '_fh', None):
            self._fh.close()

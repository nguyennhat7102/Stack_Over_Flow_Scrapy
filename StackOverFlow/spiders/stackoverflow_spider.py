"""Sequential API pages with acknowledged item processing and safe replay."""
import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import scrapy
from scrapy import signals
from scrapy.exceptions import CloseSpider
from scrapy.http import Request

from StackOverFlow.checkpoint import PageCheckpoint
from StackOverFlow.items import StackOverFlowItem
from StackOverFlow.middlewares import StaleRequest

logger = logging.getLogger(__name__)
_API_BASE = 'https://api.stackexchange.com/2.3/questions'


def _date_to_unix(d):
    return int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def _build_partitions(start_str, end_str):
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str) if end_str else datetime.now(timezone.utc).date()
    if start >= end:
        raise ValueError('PARTITION_START_DATE must precede PARTITION_END_DATE')
    result = []
    while start < end:
        stop = min(start+timedelta(days=7),end)
        result.append((start,stop))
        start = stop
    return result


def _api_url(from_date, to_date, page):
    # API dates are inclusive; subtract one second for our exclusive end boundary.
    return _API_BASE+'?'+urlencode(dict(site='stackoverflow',pagesize=100,page=page,
        fromdate=_date_to_unix(from_date),todate=_date_to_unix(to_date)-1,
        filter='default',order='asc',sort='creation'))


class StackOverFlowSpider(scrapy.Spider):
    name = 'stackoverflow'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.run_id = uuid.uuid4().hex
        self.stop_reason = None
        self.total_written = 0
        self.written_this_run = 0
        self.total_fetched = 0
        self.pages_consumed = 0
        self.partitions_processed = 0
        self._last_quota_remaining = None
        self._item_done = None

    @classmethod
    def from_crawler(cls,crawler,*args,**kwargs):
        spider = super().from_crawler(crawler,*args,**kwargs)
        crawler.signals.connect(spider.item_done,signal=signals.item_scraped)
        crawler.signals.connect(spider.item_done,signal=signals.item_dropped)
        crawler.signals.connect(spider.item_failed,signal=signals.item_error)
        return spider

    def item_done(self,item,**kwargs):
        if self._item_done:
            self._item_done.set()

    def item_failed(self,item,**kwargs):
        self.stop_reason = 'storage_error'
        self.item_done(item)

    async def start(self):
        if self.stop_reason:
            await self.crawler.engine.close_spider_async(reason=self.stop_reason)
            return
        try:
            self.partitions = _build_partitions(self.settings.get('PARTITION_START_DATE','2008-08-01'),
                                                self.settings.get('PARTITION_END_DATE',''))
            self.checkpoint = PageCheckpoint(self.settings,self.partitions)
            self.checkpoint.save(self.checkpoint.cursor)
        except (ValueError, OSError):
            logger.error('Invalid date configuration or checkpoint cannot be saved')
            await self.crawler.engine.close_spider_async(reason='startup_error')
            return
        if self.checkpoint.cursor is None:
            return
        # Validate key before requesting collection pages; this request is key-free on disk.
        yield Request('https://api.stackexchange.com/2.3/info?site=stackoverflow',
                      callback=self.authenticated,errback=self.errback,dont_filter=True,
                      meta={'run_id':self.run_id,'handle_httpstatus_all':True})

    def decode(self,response):
        try:
            body = response.json()
        except ValueError:
            raise CloseSpider('invalid_response') from None
        if not isinstance(body,dict):
            raise CloseSpider('invalid_response')
        self._last_quota_remaining = body.get('quota_remaining',self._last_quota_remaining)
        if response.meta.get('retry_exhausted'):
            logger.error('Retry exhausted | partition=%s | page=%s | status=%d',
                         response.meta.get('partition'),response.meta.get('page'),response.status)
            raise CloseSpider('retry_exhausted')
        if body.get('error_id') or response.status != 200:
            if body.get('quota_remaining') == 0 or 'quota' in str(body.get('error_message','')).lower():
                self.quota_stop(body)
            logger.error('API error | status=%d | error_id=%s | page=%s',response.status,
                         body.get('error_id'),response.meta.get('page'))
            raise CloseSpider('api_error')
        return body

    def authenticated(self,response):
        body = self.decode(response)
        if body.get('quota_remaining') == 0:
            self.quota_stop(body)
        return self.page_request(self.checkpoint.cursor)

    def page_request(self,cursor):
        index,page = cursor
        start,end = self.partitions[index]
        return Request(_api_url(start,end,page),callback=self.parse,errback=self.errback,
                       dont_filter=True,meta={'run_id':self.run_id,'partition':index,'page':page,
                                             'handle_httpstatus_all':True})

    async def parse(self,response):
        current = (response.meta['partition'],response.meta['page'])
        if current != self.checkpoint.cursor or self.stop_reason:
            return
        body = self.decode(response)
        items = body.get('items')
        if not isinstance(items,list) or type(body.get('has_more')) is not bool:
            raise CloseSpider('invalid_response')
        self.pages_consumed += 1
        self.total_fetched += len(items)
        # Keep the current-page cursor until every yielded item is durably settled.
        for q in items:
            if self.stop_reason or self.crawler.engine._slot.closing:
                break
            self._item_done = asyncio.Event()
            if isinstance(q,dict):
                item = StackOverFlowItem(question_id=q.get('question_id'),tags=q.get('tags'),
                    title=q.get('title'),creation_date=q.get('creation_date'),
                    score=q.get('score',0),answer_count=q.get('answer_count',0))
            else:
                item = StackOverFlowItem(question_id=None,tags=None)
            yield item
            await self._item_done.wait()
        self._item_done = None
        index,page = current
        start,end = self.partitions[index]
        stats = self.crawler.stats
        dropped = sum(stats.get_value('dropped/'+k,0) for k in ('insufficient_tags','duplicate_ids','invalid_schema'))
        logger.info('page=%d partition=%s/%s fetched=%d written_this_run=%d dropped=%d total_written=%d',
                    page,start,end,len(items),self.written_this_run,dropped,self.total_written)
        if self.stop_reason:
            raise CloseSpider(self.stop_reason)
        if self.crawler.engine._slot.closing:
            return
        if body['has_more'] and items and page < 25:
            next_cursor = (index,page+1)
        else:
            self.partitions_processed += 1
            if page == 25 and body['has_more']:
                logger.warning('PAGE CAP | partition=%s/%s | page=25 | remaining questions skipped',start,end)
            next_cursor = (index+1,1) if index+1 < len(self.partitions) else None
        self.checkpoint.save(next_cursor)
        if body.get('quota_remaining') == 0:
            self.quota_stop(body)
        if next_cursor is not None:
            yield self.page_request(next_cursor)

    def quota_stop(self,body):
        logger.warning('QUOTA EXHAUSTED | quota_remaining=%s | backoff=%s | reset_time=%s | total_written=%d | checkpoint_saved=True',
                       body.get('quota_remaining'),body.get('backoff'),body.get('reset_time'),self.total_written)
        raise CloseSpider('quota_exhausted')

    def errback(self,failure):
        if failure.check(StaleRequest):
            return
        logger.error('Request failed | partition=%s | page=%s | error=%s',
                     failure.request.meta.get('partition'),failure.request.meta.get('page'),
                     type(failure.value).__name__)
        raise CloseSpider('retry_exhausted' if failure.request.meta.get('retry_exhausted') else 'request_error')

    def closed(self,reason):
        self.stop_reason = reason
        stats = self.crawler.stats
        counts = {k:stats.get_value('dropped/'+k,0) for k in ('insufficient_tags','duplicate_ids','invalid_schema')}
        logger.info('FINAL SUMMARY | reason=%s | partitions_processed=%d | pages_consumed=%d | total_fetched=%d | total_written=%d | written_this_run=%d | total_dropped=%d | dropped_by_reason=%s | quota_remaining=%s',
            reason,self.partitions_processed,self.pages_consumed,self.total_fetched,self.total_written,
            self.written_this_run,sum(counts.values()),counts,self._last_quota_remaining)

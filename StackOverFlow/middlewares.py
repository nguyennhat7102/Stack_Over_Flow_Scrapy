"""Authentication, secret redaction, throttling, and bounded retries."""
import asyncio
import logging
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, quote, quote_plus

from scrapy import signals
from scrapy.downloadermiddlewares.retry import get_retry_request
from scrapy.exceptions import IgnoreRequest


class StaleRequest(IgnoreRequest):
    """Old JOBDIR queue entry superseded by the durable page cursor."""


class SecretFormatter(logging.Formatter):
    def __init__(self, original, secret):
        super().__init__()
        self.original = original or logging.Formatter()
        self.secrets = {secret, quote(secret, safe=''), quote_plus(secret)}

    def format(self, record):
        text = self.original.format(record)
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        return text


class StackOverFlowDownloaderMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        key = os.environ.get('STACKOVERFLOW_API_KEY', '').strip()
        if not key:
            raise RuntimeError('STACKOVERFLOW_API_KEY is not set or is empty. Export it before running.')
        obj = cls()
        obj.crawler = crawler
        obj._api_key = key
        obj.not_before = 0
        obj.retry_codes = set(crawler.settings.getlist('RETRY_HTTP_CODES') or [429,500,502,503,504])
        obj.retry_codes = {int(code) for code in obj.retry_codes}
        obj.redactors = []
        for handler in logging.getLogger().handlers:
            original = handler.formatter
            redactor = SecretFormatter(original, key)
            handler.setFormatter(redactor)
            obj.redactors.append((handler,original,redactor))
        crawler.signals.connect(obj.closed, signal=signals.spider_closed)
        return obj

    def closed(self):
        for handler, original, redactor in self.redactors:
            if handler.formatter is redactor:
                handler.setFormatter(original)

    @staticmethod
    def clean_url(url):
        parts = urlsplit(url)
        query = urlencode([(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k!='key'])
        return urlunsplit(parts._replace(query=query))

    async def process_request(self, request):
        spider = self.crawler.spider
        if request.meta.get('run_id') != spider.run_id or spider.stop_reason:
            raise StaleRequest('obsolete request')
        parts = urlsplit(request.url)
        if parts.scheme != 'https' or parts.netloc != 'api.stackexchange.com':
            raise RuntimeError('Only the official HTTPS Stack Exchange API is allowed')
        checkpoint = getattr(spider,'checkpoint',None)
        deadline = max(self.not_before, getattr(checkpoint,'not_before',0))
        while deadline > time.time():
            await asyncio.sleep(min(deadline-time.time(), 1))
            engine_slot = getattr(self.crawler.engine, '_slot', None) if hasattr(self.crawler,'engine') else None
            if spider.stop_reason or (engine_slot and engine_slot.closing):
                raise StaleRequest('shutdown during backoff')
        # Mutate only at the transport boundary; returning a Request would reschedule it.
        clean = self.clean_url(request.url)
        sep = '&' if '?' in clean else '?'
        request._set_url(clean + sep + urlencode({'key':self._api_key}))
        return None

    def defer(self, seconds):
        self.not_before = max(self.not_before, time.time()+max(0,float(seconds)))
        checkpoint = getattr(self.crawler.spider,'checkpoint',None)
        if checkpoint:
            checkpoint.not_before = self.not_before
            checkpoint.save(checkpoint.cursor)

    def retry(self, request, reason):
        retry = get_retry_request(request, spider=self.crawler.spider, reason=reason,
            max_retry_times=self.crawler.settings.getint('RETRY_TIMES',5))
        if retry:
            base = self.crawler.settings.getfloat('RETRY_BACKOFF_BASE',1)
            self.defer(base * 2**(retry.meta['retry_times']-1))
        return retry

    async def process_response(self, request, response):
        request._set_url(self.clean_url(request.url))
        response = response.replace(url=self.clean_url(response.url))
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = {}
        if not isinstance(body,dict):
            body = {}
        backoff = body.get('backoff')
        if type(backoff) in (int,float) and backoff > 0:
            self.defer(backoff)
        retry_after = response.headers.get(b'Retry-After')
        if retry_after:
            try: self.defer(float(retry_after))
            except ValueError: pass
        # Error ID 502 is throttle_violation, not proof that daily quota is exhausted.
        exhausted = body.get('quota_remaining') == 0 or 'quota' in str(body.get('error_message','')).lower()
        if not exhausted and (response.status in self.retry_codes or body.get('error_id') in (500, 502, 503)):
            retry = self.retry(request, f'HTTP {response.status} API {body.get("error_id", "")}')
            if retry:
                return retry
            request.meta['retry_exhausted'] = True
        return response

    def process_exception(self, request, exception):
        request._set_url(self.clean_url(request.url))
        if isinstance(exception, (StaleRequest, RuntimeError)):
            return None
        retry = self.retry(request, type(exception).__name__)
        if retry:
            return retry
        request.meta['retry_exhausted'] = True
        return None

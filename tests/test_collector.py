import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scrapy import Request
from scrapy.http import TextResponse
from scrapy.settings import Settings
from scrapy.statscollectors import MemoryStatsCollector
from scrapy.signalmanager import SignalManager
from scrapy.exceptions import DropItem

from StackOverFlow.pipelines import (TagValidationPipeline, NormalizationPipeline,
                                    DeduplicationPipeline, StoragePipeline, _load_seen_ids)
from StackOverFlow.middlewares import StackOverFlowDownloaderMiddleware
from StackOverFlow.spiders.stackoverflow_spider import StackOverFlowSpider
from tools.validate_output import validate


def record(qid=1):
    return dict(question_id=qid, tags=['python', 'scrapy'], title='Tiếng Việt',
                creation_date='2023-01-01T00:00:00Z', score=0, answer_count=0)


def crawler(path, target=2):
    c=SimpleNamespace(settings=Settings({'OUTPUT_FILE':str(path), 'TARGET_RECORDS':target,
        'PARTITION_START_DATE':'2023-01-01', 'PARTITION_END_DATE':'2023-01-08', 'JOBDIR':''}))
    c.stats=MemoryStatsCollector(c); c.signals=SignalManager(c)
    c.spider=SimpleNamespace(total_written=0, written_this_run=0, stop_reason=None)
    return c


class RegressionTests(unittest.TestCase):
    def test_start_is_class_method(self):
        self.assertIn('start', StackOverFlowSpider.__dict__)

    def test_schema_rejected_by_validator(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.jsonl'
            for row in ({'tags':['python','python']}, [], dict(record(),question_id=True),
                        dict(record(),tags=['Python','python']), dict(record(),creation_date='bad')):
                p.write_text(json.dumps(row)+'\n')
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(validate(str(p),1),1)

    def test_integrity_and_schema(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.jsonl'; good=(json.dumps(record())+'\n').encode()
            p.write_bytes(good+b'{"question_id":')
            self.assertEqual(_load_seen_ids(str(p)),({1},len(good)))
            p.write_bytes(b'bad\n'+good)
            with self.assertRaises(RuntimeError): _load_seen_ids(str(p))
            p.write_text('{"tags": ["a", "b"]}\n')
            with self.assertRaises(RuntimeError): _load_seen_ids(str(p))

    def test_pipeline_exact_limit_normalization_and_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.jsonl'; p.write_text(json.dumps(record()))
            c=crawler(p)
            pipes=[cls.from_crawler(c) for cls in (TagValidationPipeline,NormalizationPipeline,
                                                 DeduplicationPipeline,StoragePipeline)]
            for pipe in pipes:
                if hasattr(pipe,'open_spider'): pipe.open_spider()
            for qid in (1,2,3):
                item=dict(record(qid),tags=[' Python ', 'python','Scrapy'],creation_date=1672531200)
                try:
                    for pipe in pipes: item=pipe.process_item(item)
                except DropItem: pass
            pipes[-1].close_spider()
            rows=[json.loads(x) for x in p.read_text().splitlines()]
            self.assertEqual([r['question_id'] for r in rows],[1,2])
            self.assertEqual(rows[1]['tags'],['python','scrapy'])
            self.assertEqual(c.spider.total_written,2)
            self.assertEqual(c.spider.written_this_run,1)

    def test_missing_key_is_fatal(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(RuntimeError):
                StackOverFlowDownloaderMiddleware.from_crawler(crawler('unused'))

    def test_middleware_does_not_reschedule_and_scrubs_key(self):
        async def run():
            c=crawler('unused'); c.spider.run_id='test'
            with patch.dict('os.environ',{'STACKOVERFLOW_API_KEY':'dummy-secret'}):
                mw=StackOverFlowDownloaderMiddleware.from_crawler(c)
            r=Request('https://api.stackexchange.com/2.3/questions',meta={'run_id':'test'})
            result=await mw.process_request(r)
            self.assertIsNone(result)
            self.assertIn('key=dummy-secret',r.url)
            response=TextResponse(r.url,request=r,body=b'{"items":[],"quota_remaining":1}',encoding='utf-8')
            response=await mw.process_response(r,response)
            self.assertNotIn('dummy-secret',r.url)
            self.assertNotIn('dummy-secret',response.url)
        asyncio.run(run())

    def test_write_failure_does_not_commit_id(self):
        with tempfile.TemporaryDirectory() as d:
            c=crawler(Path(d)/'x.jsonl')
            dedup=DeduplicationPipeline.from_crawler(c); dedup.open_spider()
            storage=StoragePipeline.from_crawler(c); storage.open_spider()
            item=record()
            dedup.process_item(item)
            with patch.object(storage,'_fh') as fh:
                fh.write.side_effect=OSError('disk full')
                with self.assertRaises(OSError): storage.process_item(item)
            self.assertEqual(dedup.seen_ids,set())
            self.assertEqual(dedup.pending,set())
            self.assertEqual(c.spider.total_written,0)
            storage.close_spider()

    def test_date_partitions_have_exclusive_end_and_short_last_week(self):
        from urllib.parse import parse_qs,urlsplit
        from StackOverFlow.spiders.stackoverflow_spider import _build_partitions,_api_url
        partitions=_build_partitions('2023-01-01','2023-01-10')
        self.assertEqual(len(partitions),2)
        first=parse_qs(urlsplit(_api_url(*partitions[0],1)).query)
        second=parse_qs(urlsplit(_api_url(*partitions[1],1)).query)
        self.assertEqual(int(first['todate'][0])+1,int(second['fromdate'][0]))
        self.assertEqual((partitions[-1][1]-partitions[-1][0]).days,2)


if __name__ == '__main__': unittest.main()

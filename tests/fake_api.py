"""Offline transport fixture. No socket or user credential is used."""
import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from scrapy.http import TextResponse


class Handler:
    lazy = True
    def __init__(self,crawler):
        self.crawler=crawler
        self.attempts={}

    @classmethod
    def from_crawler(cls,crawler): return cls(crawler)

    async def download_request(self,request):
        config=self.crawler.settings
        mode=config.get('FAKE_MODE','normal')
        query=parse_qs(urlsplit(request.url).query)
        assert query.get('key') == ['offline-test-key']
        page=int(query.get('page',['0'])[0])
        partition=request.meta.get('partition',-1)
        self.attempts[page]=self.attempts.get(page,0)+1
        with Path(config['FAKE_TRACE']).open('a') as fh:
            fh.write(json.dumps(dict(page=page,partition=partition,time=time.time()))+'\n')
        if mode=='slow': await asyncio.sleep(0.2)
        if mode=='retry' and page==1:
            return TextResponse(request.url,status=503,request=request,body=b'{}',encoding='utf-8')
        if mode=='invalid_key' and page==0:
            return TextResponse(request.url,status=400,request=request,
                                body=b'{"error_id": 400, "error_message": "bad key"}',encoding='utf-8')
        if page==0:
            body={'items':[{}],'quota_remaining':100,'has_more':False}
        else:
            start=partition*10000+(page-1)*100
            body={'items':[dict(question_id=start+i,tags=[' Python ','python','scrapy'],title='Việt',
                          creation_date=1672531200,score=0,answer_count=0) for i in range(1,101)],
                  'has_more':True,'quota_remaining':100-page}
            if mode=='mixed' and page==1:
                body['items'][0]['tags']=['Python','python']
                body['items'][1]['question_id']=None
                body['items'][2]['tags']=[{},'python']
                body['items'][3]['question_id']=5
            if mode=='empty':
                body['items']=[]
                body['has_more']=False
            if mode=='quota': body['quota_remaining']=0
            if mode=='backoff' and page==1: body['backoff']=0.2
        return TextResponse(request.url,request=request,body=json.dumps(body).encode(),encoding='utf-8')

    async def close(self): pass

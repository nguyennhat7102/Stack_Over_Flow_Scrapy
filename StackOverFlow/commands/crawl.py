"""Preserve scrapy crawl syntax while making runtime failures observable to shell gates."""
from scrapy.commands.crawl import Command as CrawlCommand


class Command(CrawlCommand):
    def _create_crawler(self, name):
        self.collector_crawler = super()._create_crawler(name)
        return self.collector_crawler

    def run(self,args,opts):
        super().run(args,opts)
        crawler = getattr(self,'collector_crawler',None)
        if crawler and crawler.spider:
            reason = crawler.stats.get_value('finish_reason')
            if crawler.stats.get_value('spider_exceptions/count', 0):
                self.exitcode = 1
            elif reason == 'quota_exhausted':
                self.exitcode = 2
            elif reason not in ('finished','target_reached','target_already_met','shutdown'):
                self.exitcode = 1

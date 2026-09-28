# Scrapy settings for the StackOverFlow project
#
# See documentation in:
#     https://docs.scrapy.org/en/latest/topics/settings.html

BOT_NAME = "StackOverFlow"

SPIDER_MODULES = ["StackOverFlow.spiders"]
NEWSPIDER_MODULE = "StackOverFlow.spiders"

ADDONS = {}

# ── Crawl identity ────────────────────────────────────────────────────────────
# Do NOT obey robots.txt — we are calling a JSON API endpoint, not scraping HTML.
ROBOTSTXT_OBEY = False

# ── Rate control ──────────────────────────────────────────────────────────────
# Fixed baseline rate; API backoff may require a longer wait.
CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1  # seconds between requests
DOWNLOAD_DELAY_JITTER = 0

# ── Retry configuration ───────────────────────────────────────────────────────
# FR-013: Retry transient errors up to 5 times with exponential back-off.
# Note: HTTP 400 quota exhaustion is handled separately in the spider.
RETRY_ENABLED = True
RETRY_TIMES = 5
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

# ── Checkpoint / resume ───────────────────────────────────────────────────────
# FR-005: Scrapy JOBDIR persists the request queue for pause-and-resume.
JOBDIR = "crawl_jobs/stackoverflow"

# ── Output ────────────────────────────────────────────────────────────────────
# FR-004 / FR-019: NDJSON crawl-time output; UTF-8 encoded.
OUTPUT_FILE = "output/transactions.jsonl"

# ── Collection target ─────────────────────────────────────────────────────────
# FR-010 / FR-015: Stop immediately after this many distinct valid transactions.
TARGET_RECORDS = 100_000

# ── Date partitioning ─────────────────────────────────────────────────────────
# FR-017: 7-day partitions; empty string for PARTITION_END_DATE = today (UTC).
PARTITION_START_DATE = "2008-08-01"  # Stack Overflow launch date
PARTITION_END_DATE = ""              # Empty → today at crawl time

# ── Downloader middlewares ─────────────────────────────────────────────────────
# Auth middleware owns bounded retries and backoff; disable duplicate retry handling.
DOWNLOADER_MIDDLEWARES = {
    "StackOverFlow.middlewares.StackOverFlowDownloaderMiddleware": 543,
    "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
}

# ── Item pipelines ─────────────────────────────────────────────────────────────
# Pipeline execution order is fixed by the project constitution:
#   TagValidation(100) → Normalization(200) → Deduplication(300) → Storage(400)
ITEM_PIPELINES = {
    "StackOverFlow.pipelines.TagValidationPipeline": 100,
    "StackOverFlow.pipelines.NormalizationPipeline": 200,
    "StackOverFlow.pipelines.DeduplicationPipeline": 300,
    "StackOverFlow.pipelines.StoragePipeline": 400,
}

# ── Encoding ──────────────────────────────────────────────────────────────────
FEED_EXPORT_ENCODING = "utf-8"

# ── Telnet console ────────────────────────────────────────────────────────────
TELNETCONSOLE_ENABLED = False

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"

# Runtime contract (Scrapy 2.19)
COMMANDS_MODULE = "StackOverFlow.commands"
CONCURRENT_ITEMS = 1
RETRY_BACKOFF_BASE = 1
REDIRECT_ENABLED = False
REMOTE_CONTROL_ENABLED = False
SCHEDULER = "StackOverFlow.scheduler.RecoveringScheduler"
# collector.json owns checkpoint state; do not load unrelated legacy pickle state.
EXTENSIONS = {"scrapy.extensions.spiderstate.SpiderState": None}

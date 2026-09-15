from pathlib import Path

BOT_NAME = "jobscraper"

SPIDER_MODULES = ["jobscraper.spiders"]
NEWSPIDER_MODULE = "jobscraper.spiders"

# Public JSON APIs are meant to be fetched; robots.txt is for HTML crawls.
ROBOTSTXT_OBEY = False
DOWNLOAD_DELAY = 0.1
CONCURRENT_REQUESTS = 24
CONCURRENT_REQUESTS_PER_DOMAIN = 8
DOWNLOAD_TIMEOUT = 20
RETRY_TIMES = 1

USER_AGENT = "JobDiscovery/1.0 (personal job search aggregator; +https://github.com/scrapy/scrapy)"

LOG_LEVEL = "INFO"
TELNETCONSOLE_ENABLED = False

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data"
_DATA.mkdir(exist_ok=True)

FEEDS = {
    str(_DATA / "jobs.json"): {
        "format": "json",
        "encoding": "utf-8",
        "overwrite": True,
        "indent": 2,
        "item_export_kwargs": {"ensure_ascii": False},
    }
}

ITEM_PIPELINES = {
    "jobscraper.pipelines.DedupePipeline": 300,
}

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

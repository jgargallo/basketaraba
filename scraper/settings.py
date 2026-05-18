from scraper.common import USER_AGENT

BOT_NAME = "basketaraba"

SPIDER_MODULES = ["scraper.spiders"]
NEWSPIDER_MODULE = "scraper.spiders"

USER_AGENT = USER_AGENT
ROBOTSTXT_OBEY = False
DOWNLOAD_DELAY = 0.4
CONCURRENT_REQUESTS_PER_DOMAIN = 4
RETRY_ENABLED = True
RETRY_TIMES = 4
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504, 522, 524]
RETRY_PRIORITY_ADJUST = -1
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.4
AUTOTHROTTLE_MAX_DELAY = 6.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = False
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es,en;q=0.9",
}
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_DATEFORMAT = "%H:%M:%S"


from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scraper.settings as scraper_settings


def _make_flaky_handler(fail_status: int):
    class _FlakyHandler(BaseHTTPRequestHandler):
        attempts = 0

        def do_GET(self) -> None:
            type(self).attempts += 1
            if type(self).attempts == 1:
                self.send_response(fail_status)
                self.end_headers()
                self.wfile.write(b"temporary failure")
                return

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args) -> None:
            return

    return _FlakyHandler


def _run_retry_probe(fail_status: int) -> dict:
    handler = _make_flaky_handler(fail_status)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{server.server_address[1]}/flaky"
    script = """
import json
import scrapy
from scrapy.crawler import CrawlerProcess

url = __import__('sys').argv[1]
fail_status = int(__import__('sys').argv[2])

class RetryProbeSpider(scrapy.Spider):
    name = 'retry-probe'
    custom_settings = {
        'RETRY_ENABLED': True,
        'RETRY_TIMES': 2,
        'RETRY_HTTP_CODES': [429, 503],
        'LOG_LEVEL': 'ERROR',
    }

    def start_requests(self):
        yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        return None

process = CrawlerProcess()
crawler = process.create_crawler(RetryProbeSpider)
process.crawl(crawler)
process.start()
stats = crawler.stats.get_stats()
print(json.dumps({
    'retry_count': stats.get('retry/count', 0),
    'http_200': stats.get('downloader/response_status_count/200', 0),
    'failed_status': fail_status,
    'finish_reason': stats.get('finish_reason')
}))
"""

    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, url, str(fail_status)],
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    return json.loads(completed.stdout.strip())


class ScrapyRetryRegressionTests(unittest.TestCase):
    def test_retry_count_increments_on_flaky_endpoint(self) -> None:
        stats = _run_retry_probe(503)
        self.assertGreaterEqual(stats["retry_count"], 1)
        self.assertEqual(1, stats["http_200"])
        self.assertEqual("finished", stats["finish_reason"])

    def test_retry_count_increments_on_rate_limit(self) -> None:
        stats = _run_retry_probe(429)
        self.assertGreaterEqual(stats["retry_count"], 1)
        self.assertEqual(1, stats["http_200"])
        self.assertEqual(429, stats["failed_status"])
        self.assertEqual("finished", stats["finish_reason"])

    def test_scrapy_settings_keep_network_hardening_enabled(self) -> None:
        self.assertTrue(scraper_settings.RETRY_ENABLED)
        self.assertIn(429, scraper_settings.RETRY_HTTP_CODES)
        self.assertIn(503, scraper_settings.RETRY_HTTP_CODES)
        self.assertTrue(scraper_settings.AUTOTHROTTLE_ENABLED)
        self.assertGreaterEqual(scraper_settings.AUTOTHROTTLE_MAX_DELAY, scraper_settings.AUTOTHROTTLE_START_DELAY)
        self.assertGreater(scraper_settings.AUTOTHROTTLE_TARGET_CONCURRENCY, 0)


if __name__ == "__main__":
    unittest.main()
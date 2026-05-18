from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from scraper.spiders.basketaraba import BasketarabaSpider


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the embedded Scrapy basketaraba spider")
    parser.add_argument("group", help="Group name, e.g. 'SENIOR MASCULINA 3ª-GRUPO A'")
    parser.add_argument("--out", type=Path, default=Path("data"), help="Output root directory (default: ./data)")
    parser.add_argument("--metrics-out", type=Path, help="Write run metrics to a JSON file")
    parser.add_argument("--metrics-history-dir", type=Path,
                        help="Write timestamped metrics snapshots under the given root directory")
    parser.add_argument("--sleep", type=float, default=None, help="Override Scrapy download delay in seconds")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached files exist")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose Scrapy logging")
    args = parser.parse_args(list(argv) if argv is not None else None)

    os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "scraper.settings")
    settings = get_project_settings()
    if args.metrics_out is not None:
        os.environ["BASKETARABA_METRICS_OUT"] = str(args.metrics_out)
    else:
        os.environ.pop("BASKETARABA_METRICS_OUT", None)
    if args.metrics_history_dir is not None:
        os.environ["BASKETARABA_METRICS_HISTORY_DIR"] = str(args.metrics_history_dir)
    else:
        os.environ.pop("BASKETARABA_METRICS_HISTORY_DIR", None)
    if args.sleep is not None:
        settings.set("DOWNLOAD_DELAY", args.sleep, priority="cmdline")
    if args.verbose:
        settings.set("LOG_LEVEL", "DEBUG", priority="cmdline")
    process = CrawlerProcess(settings, install_root_handler=False)
    process.crawl(BasketarabaSpider, group=args.group, out=str(args.out), force=args.force)
    process.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

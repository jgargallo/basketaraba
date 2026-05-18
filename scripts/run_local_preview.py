#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import socket
import subprocess
import sys
from pathlib import Path
import re
import unicodedata


DEFAULT_GROUP = "SENIOR MASCULINA 3ª-GRUPO A"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only.lower()).strip("-")
    return re.sub(r"-+", "-", cleaned)


def run_step(command: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def find_available_port(preferred_port: int, host: str = "127.0.0.1", attempts: int = 20) -> int:
    for port in range(preferred_port, preferred_port + attempts):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {preferred_port}-{preferred_port + attempts - 1}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh basketaraba data, rebuild the static site, and optionally serve web/dist locally.",
    )
    parser.add_argument("--group", default=DEFAULT_GROUP, help=f"Group name to process (default: {DEFAULT_GROUP})")
    parser.add_argument("--engine", choices=("requests", "scrapy"), help="Override crawler engine")
    parser.add_argument("--force", action="store_true", help="Refresh crawler cache before rebuilding")
    parser.add_argument("--skip-crawl", action="store_true", help="Skip crawler.py")
    parser.add_argument("--skip-stats", action="store_true", help="Skip stats.py")
    parser.add_argument("--skip-build", action="store_true", help="Skip web/build.py")
    parser.add_argument("--no-serve", action="store_true", help="Do not start the local HTTP server")
    parser.add_argument("--port", type=int, default=8000, help="Local preview port (default: 8000)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    group_slug = slugify(args.group)
    group_dir = repo_root / "data" / group_slug
    database_path = group_dir / "database.json"
    dist_dir = repo_root / "web" / "dist"

    crawler_command = [sys.executable, "crawler.py", args.group]
    if args.engine:
        crawler_command.extend(["--engine", args.engine])
    if args.force:
        crawler_command.append("--force")

    if not args.skip_crawl:
        run_step(crawler_command, repo_root)

    if not args.skip_stats:
        run_step([sys.executable, "stats.py", str(group_dir)], repo_root)

    if not args.skip_build:
        run_step([sys.executable, "web/build.py", str(database_path)], repo_root)

    if args.no_serve:
        print(f"Static site rebuilt in {dist_dir}", flush=True)
        return 0

    selected_port = find_available_port(args.port)
    if selected_port != args.port:
        print(f"Port {args.port} is busy; using {selected_port} instead.", flush=True)
    print(f"Serving {dist_dir} on http://127.0.0.1:{selected_port}", flush=True)
    run_step([sys.executable, "-m", "http.server", str(selected_port)], dist_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
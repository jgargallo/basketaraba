#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import re
import socket
import subprocess
import sys
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers (shared pattern with run_local_preview.py)
# ---------------------------------------------------------------------------

def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only.lower()).strip("-")
    return re.sub(r"-+", "-", cleaned)


def run_step(command: list[str], cwd: Path) -> None:
    """Run a command, streaming output directly (for build step)."""
    print(f"  $ {' '.join(str(c) for c in command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def run_step_quiet(label: str, command: list[str], cwd: Path) -> bool:
    """Run a command silently; print label + OK/FAILED. Dump last lines on failure."""
    print(f"  {label} ...", end=" ", flush=True)
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True
    )
    if result.returncode == 0:
        print("OK", flush=True)
        return True
    print(f"FAILED (exit {result.returncode})", flush=True)
    combined = (result.stdout + result.stderr).strip().splitlines()
    tail = combined[-15:] if len(combined) > 15 else combined
    for line in tail:
        print(f"    {line}", flush=True)
    return False


def find_available_port(preferred_port: int, host: str = "127.0.0.1", attempts: int = 20) -> int:
    for port in range(preferred_port, preferred_port + attempts):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {preferred_port}-{preferred_port + attempts - 1}")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def list_groups_from_crawler(repo_root: Path, *, full_season: bool = False) -> list[dict]:
    """Return list of group dicts: {name, heading, category_id}."""
    cmd = [sys.executable, "crawler.py", "--list-groups"]
    if full_season:
        cmd.append("--full-season")
    result = subprocess.run(
        cmd,
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    # Support both old list[str] and new list[dict] formats.
    if data and isinstance(data[0], str):
        return [{"name": n, "heading": None, "category_id": None} for n in data]
    return data


def list_groups_from_data(repo_root: Path) -> list[dict]:
    """Return group dicts from existing data/*/database.json files."""
    groups = []
    for db_path in sorted((repo_root / "data").glob("*/database.json")):
        try:
            with db_path.open() as f:
                db = json.load(f)
            name = db.get("group", {}).get("group_name")
            if name:
                groups.append({"name": name, "heading": None, "category_id": None})
        except Exception:
            pass
    return groups


def process_group(
    group_name: str,
    repo_root: Path,
    *,
    skip_crawl: bool,
    skip_stats: bool,
    force: bool,
    engine: str | None,
    category_id: str | None = None,
    heading: str | None = None,
) -> bool:
    """Run crawler + stats for one group. Returns True if both steps succeeded."""
    group_slug = slugify(group_name)
    group_dir = repo_root / "data" / group_slug

    group_dir.mkdir(parents=True, exist_ok=True)

    if not skip_crawl:
        crawler_cmd = [sys.executable, "crawler.py", group_name]
        if engine:
            crawler_cmd.extend(["--engine", engine])
        if force:
            crawler_cmd.append("--force")
        if category_id:
            crawler_cmd.extend(["--category-id", category_id])
        if heading:
            crawler_cmd.extend(["--heading", heading])
        if not run_step_quiet("crawl", crawler_cmd, repo_root):
            return False
        # Scrapy exits 0 even on spider failure — verify output was actually written.
        if not (group_dir / "group.json").exists():
            print("  crawl produced no output (group not found on site) — skipping", flush=True)
            return False

    if not skip_stats:
        if not (group_dir / "group.json").exists():
            print("  stats skipped — no crawl output in", group_dir.name, flush=True)
            return False
        if not run_step_quiet("stats", [sys.executable, "stats.py", str(group_dir)], repo_root):
            return False

    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full basketaraba pipeline for all groups: "
            "crawler -> stats -> web/build."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Pass --force to crawler (refresh cache)")
    parser.add_argument("--full-season", action="store_true", help="Scan all 30 past weeks when discovering groups (default: current + previous week only)")
    parser.add_argument("--engine", choices=("requests", "scrapy"), help="Override crawler engine")
    parser.add_argument("--skip-crawl", action="store_true", help="Skip crawler.py for all groups")
    parser.add_argument("--skip-stats", action="store_true", help="Skip stats.py for all groups")
    parser.add_argument("--skip-build", action="store_true", help="Skip web/build.py")
    parser.add_argument("--no-serve", action="store_true", help="Do not start the local HTTP server after build")
    parser.add_argument("--port", type=int, default=8000, help="Local preview port (default: 8000)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    dist_dir = repo_root / "web" / "dist"

    # ------------------------------------------------------------------
    # 1. Discover groups
    # ------------------------------------------------------------------
    if args.skip_crawl:
        print("--skip-crawl set: loading group list from existing data/*/database.json ...", flush=True)
        groups = list_groups_from_data(repo_root)
    else:
        print("Fetching group list from crawler.py ...", flush=True)
        try:
            groups = list_groups_from_crawler(repo_root, full_season=args.full_season)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not retrieve group list: {exc}", flush=True)
            return 1

    if not groups:
        print("No groups found. Nothing to do.", flush=True)
        return 0

    # ------------------------------------------------------------------
    # 2. Register directories for any newly discovered group
    # ------------------------------------------------------------------
    for group in groups:
        group_name = group["name"] if isinstance(group, dict) else group
        group_dir = repo_root / "data" / slugify(group_name)
        if not group_dir.exists():
            print(f"  [NEW] {group_name}  →  {group_dir.name}/", flush=True)
            group_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 3. Process each group
    # ------------------------------------------------------------------
    print(f"\n=== Processing {len(groups)} group(s) ===", flush=True)

    succeeded: list[str] = []
    failed: list[str] = []

    for idx, group in enumerate(groups, start=1):
        if isinstance(group, dict):
            group_name = group["name"]
            category_id = group.get("category_id")
            heading = group.get("heading")
        else:
            group_name = group
            category_id = None
            heading = None
        group_dir = repo_root / "data" / slugify(group_name)
        already_complete = (group_dir / "database.json").exists()
        label = "EXISTS" if already_complete else "NEW"
        print(f"\n[{idx}/{len(groups)}] [{label}] {group_name}", flush=True)
        ok = process_group(
            group_name,
            repo_root,
            skip_crawl=args.skip_crawl,
            skip_stats=args.skip_stats,
            force=args.force,
            engine=args.engine,
            category_id=category_id,
            heading=heading,
        )
        if ok:
            print("  OK", flush=True)
            succeeded.append(group_name)
        else:
            print("  FAILED", flush=True)
            failed.append(group_name)

    # ------------------------------------------------------------------
    # 3. Build site from all succeeded groups
    # ------------------------------------------------------------------
    print(f"\n=== Summary ===", flush=True)
    print(f"Succeeded: {len(succeeded)}  {', '.join(succeeded) if succeeded else '-'}", flush=True)
    print(f"Failed:    {len(failed)}  {', '.join(failed) if failed else '-'}", flush=True)

    if not args.skip_build:
        db_paths = [
            str(repo_root / "data" / slugify(name) / "database.json")
            for name in succeeded
            if (repo_root / "data" / slugify(name) / "database.json").exists()
        ]
        if not db_paths:
            print("\nNo database files available; skipping web/build.py.", flush=True)
        else:
            print(f"\nBuilding site from {len(db_paths)} database file(s) ...", flush=True)
            run_step([sys.executable, "web/build.py"] + db_paths, repo_root)

    # ------------------------------------------------------------------
    # 4. Optionally serve
    # ------------------------------------------------------------------
    if args.no_serve or args.skip_build:
        if not args.skip_build:
            print(f"Static site rebuilt in {dist_dir}", flush=True)
        return 1 if failed else 0

    selected_port = find_available_port(args.port)
    if selected_port != args.port:
        print(f"Port {args.port} is busy; using {selected_port} instead.", flush=True)
    print(f"Serving {dist_dir} on http://127.0.0.1:{selected_port}", flush=True)
    run_step([sys.executable, "-m", "http.server", str(selected_port)], dist_dir)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

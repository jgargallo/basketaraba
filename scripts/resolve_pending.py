#!/usr/bin/env python3
"""
Resolve pending match dates without running a full crawl.

For each group that has pending_dates.json, scans the weeks around each
unscheduled match (±4 weeks) to find its starts_at date, then updates
matches.json and shrinks pending_dates.json.

Usage:
    python scripts/resolve_pending.py
    python scripts/resolve_pending.py data/senior-masculina-3a-grupo-a
    python scripts/resolve_pending.py --sleep 0.2 -v
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler import Client, fetch_week_jornada, _PENDING_SCAN_RADIUS
from scraper.common import (
    parse_week_jornada as _parse_week_jornada,
    write_json as _write_json,
    write_raw as _write_raw,
)

log = logging.getLogger("resolve_pending")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_group(group_dir: Path, client: Client) -> tuple[int, int]:
    """Return (resolved_count, still_pending_count)."""
    pending_path = group_dir / "pending_dates.json"
    matches_path = group_dir / "matches.json"

    if not pending_path.exists() or not matches_path.exists():
        return 0, 0

    pending: list[dict] = _load_json(pending_path)
    if not pending:
        return 0, 0

    matches_data: dict = _load_json(matches_path)
    group_meta: dict = matches_data["group"]
    category_id: str = group_meta["category_id"]

    # Build team lookups from the existing match index.
    name_to_id: dict[str, str] = {}
    logo_to_id: dict[str, str] = {}
    for m in matches_data["matches"]:
        for side in ("home", "away"):
            tid = m.get(f"{side}_team_id")
            name = m.get(f"{side}_team")
            logo = m.get(f"{side}_logo")
            if tid and name:
                name_to_id[name] = tid
            if tid and logo:
                logo_to_id[logo] = tid

    def _resolve(name: str | None, logo: str | None) -> str:
        if name and name in name_to_id:
            return name_to_id[name]
        if logo and logo in logo_to_id:
            return logo_to_id[logo]
        return ""

    pending_pairs: set[tuple[str, str]] = {
        (p["home_team_id"], p["away_team_id"]) for p in pending
    }

    # Collect all weeks to scan across all pending entries.
    weeks_to_scan: set[date] = set()
    for p in pending:
        if p.get("monday"):
            base = date.fromisoformat(p["monday"])
            for offset in range(-_PENDING_SCAN_RADIUS, _PENDING_SCAN_RADIUS + 1):
                weeks_to_scan.add(base + timedelta(weeks=offset))

    raw_dir = group_dir / "raw"
    resolved: dict[tuple[str, str], str] = {}  # pair → starts_at

    for week in sorted(weeks_to_scan):
        if not pending_pairs:
            break
        raw_path = raw_dir / f"jornada_{week.isoformat()}.html"
        try:
            html, _ = fetch_week_jornada(client, category_id, week)
            _write_raw(raw_path, html)
        except Exception as exc:
            log.warning("  week %s: %s", week, exc)
            continue

        for _g, entry in _parse_week_jornada(html):
            if not entry.starts_at:
                continue
            home_id = _resolve(entry.home_team, entry.home_logo)
            away_id = _resolve(entry.away_team, entry.away_logo)
            key = (home_id, away_id)
            if key not in pending_pairs:
                continue
            log.info("  Resolved: %s vs %s → %s", entry.home_team, entry.away_team, entry.starts_at)
            resolved[key] = entry.starts_at
            pending_pairs.discard(key)

    # Patch starts_at in matches.json for every resolved pair.
    if resolved:
        for m in matches_data["matches"]:
            key = (m.get("home_team_id"), m.get("away_team_id"))
            if key in resolved and not m.get("starts_at"):
                m["starts_at"] = resolved[key]
        _write_json(matches_path, matches_data)

    # Shrink pending_dates.json.
    still_pending = [p for p in pending if (p["home_team_id"], p["away_team_id"]) in pending_pairs]
    _write_json(pending_path, still_pending)

    return len(resolved), len(still_pending)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("groups", nargs="*", type=Path, metavar="group_dir",
                        help="Group data directories (default: all data/*/)")
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="Root data directory (default: data/)")
    parser.add_argument("--sleep", type=float, default=0.4,
                        help="Seconds between HTTP requests (default: 0.4)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.groups:
        group_dirs = [Path(g) for g in args.groups]
    else:
        group_dirs = sorted(args.data.glob("*/"))

    group_dirs = [g for g in group_dirs if (g / "pending_dates.json").exists()]
    if not group_dirs:
        print("No groups with pending_dates.json found.")
        return 0

    client = Client(sleep=args.sleep)
    total_resolved = total_pending = 0

    for group_dir in group_dirs:
        pending_before = len(_load_json(group_dir / "pending_dates.json"))
        if pending_before == 0:
            continue
        print(f"{group_dir.name} ({pending_before} pending) …", end=" ", flush=True)
        resolved, still = resolve_group(group_dir, client)
        print(f"resolved {resolved}, still pending {still}")
        total_resolved += resolved
        total_pending += still

    print(f"\nTotal: resolved {total_resolved}, still pending {total_pending}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
One-shot migration: move data/<group>/ → data/<season>/<group>/

Detects the season from the earliest date in group.json's season_jornadas.
  month >= 8  →  "<year>-<year+1 % 100:02d>"   (e.g. "2025-26")
  month < 8   →  "<year-1>-<year % 100:02d>"    (e.g. "2024-25")

Run with --dry-run first to review, then without to execute.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def detect_season(season_jornadas: dict) -> str:
    if not season_jornadas:
        raise ValueError("season_jornadas is empty")
    first_date = min(season_jornadas.values())  # "YYYY-MM-DD"
    year, month = int(first_date[:4]), int(first_date[5:7])
    if month >= 8:
        return f"{year}-{(year + 1) % 100:02d}"
    return f"{year - 1}-{year % 100:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Print planned moves without executing")
    p.add_argument("--data-dir", type=Path, default=None, help="Override data directory (default: <repo>/data)")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    data_dir = args.data_dir or repo_root / "data"

    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    moves: list[tuple[Path, Path]] = []
    errors: list[str] = []

    for group_dir in sorted(data_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        group_json = group_dir / "group.json"
        if not group_json.exists():
            print(f"  SKIP {group_dir.name}/ — no group.json", flush=True)
            continue
        try:
            data = json.loads(group_json.read_text(encoding="utf-8"))
            season_jornadas = data.get("season_jornadas", {})
            season = detect_season(season_jornadas)
        except Exception as exc:
            errors.append(f"{group_dir.name}: {exc}")
            continue

        dest = data_dir / season / group_dir.name
        if dest == group_dir:
            print(f"  SKIP {group_dir.name}/ — already at {season}/{group_dir.name}/", flush=True)
            continue
        moves.append((group_dir, dest))

    if errors:
        print("\nErrors detecting season:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)

    if not moves:
        print("Nothing to move.")
        return 0

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Planned moves ({len(moves)}):")
    for src, dst in moves:
        print(f"  {src.relative_to(data_dir)}  →  {dst.relative_to(data_dir)}")

    print("\ngit mv commands:")
    for src, dst in moves:
        print(f"  git mv {src.relative_to(repo_root)} {dst.relative_to(repo_root)}")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to execute.")
        return 0

    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        print(f"  Moved {src.name} → {dst.parent.name}/{dst.name}", flush=True)

    print(f"\nMigrated {len(moves)} group(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

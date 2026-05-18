#!/usr/bin/env python3
"""
basketaraba.com crawler.

Downloads, for a given group of the basketaraba league:
  * the season calendar (jornadas + matchups)
  * every match's player stats per team
  * every match's play-by-play log

Output layout (under --out, default ./data/<group_slug>):
    group.json              group/category metadata + team roster
    matches.json            index of all matches in the season
    matches/<id>.json       per-match structured data
    raw/calendario.html
    raw/jornada_<YYYY-MM-DD>.html
    raw/partido_<id>.html

Usage:
    python crawler.py "SENIOR MASCULINA 3ª-GRUPO A"
    python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --out ./data --sleep 0.5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from scraper.common import (
    BASE,
    USER_AGENT,
    GroupRef,
    JornadaMatchEntry,
    MatchDetail,
    SeasonCalendar,
    ddmmyyyy_to_monday as _ddmmyyyy_to_monday,
    extract_group_id as _extract_group_id,
    norm_text as _norm,
    parse_calendar as _parse_calendar,
    parse_match as _parse_match,
    parse_week_jornada as _parse_week_jornada,
    slugify as _slugify,
    to_dict as _to_dict,
    write_json as _write_json,
    write_raw as _write_raw,
)

log = logging.getLogger("basketaraba")


def _write_metrics_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_metrics_snapshot(root: Path, group_name: str, label: str, payload: dict) -> Path:
    timestamp = datetime.now()
    snapshot_path = root / _slugify(group_name) / timestamp.strftime("%Y-%m-%d") / f"{timestamp.strftime('%H%M%S')}_{label}.json"
    _write_metrics_file(snapshot_path, payload)
    return snapshot_path


def _emit_metrics(metrics: dict) -> None:
    metrics_out = os.environ.get("BASKETARABA_METRICS_OUT")
    if metrics_out:
        _write_metrics_file(Path(metrics_out), metrics)
    if os.environ.get("BASKETARABA_EMIT_METRICS_JSON") == "1":
        print(f"METRICS_JSON: {json.dumps(metrics, sort_keys=True)}", flush=True)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, sleep: float = 0.4):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.sleep = sleep
        self.request_count = 0

    def get(self, url: str) -> str:
        log.debug("GET %s", url)
        self.request_count += 1
        r = self.s.get(url, timeout=30)
        r.raise_for_status()
        time.sleep(self.sleep)
        return r.text


# ---------------------------------------------------------------------------
# Group / category resolution
# ---------------------------------------------------------------------------


def resolve_group(client: Client, group_name: str) -> GroupRef:
    """Resolve a group like 'SENIOR MASCULINA 3ª-GRUPO A' to its IDs."""
    target = _norm(group_name)

    # Find category — the part before "-GRUPO" (or full name if no group split).
    if "-GRUPO" in target:
        category_name = target.split("-GRUPO", 1)[0].strip()
    else:
        category_name = target

    html = client.get(f"{BASE}/jornada")
    soup = BeautifulSoup(html, "lxml")

    categoria_select = soup.select_one("#categoria")
    if not categoria_select:
        raise RuntimeError("Could not find categoria <select> on jornada page")

    category_id = None
    for opt in categoria_select.select("option"):
        if not opt.get("value"):
            continue
        if _norm(opt.get_text()) == category_name:
            category_id = opt["value"]
            break
    if not category_id:
        opts = [o.get_text(strip=True) for o in categoria_select.select("option") if o.get("value")]
        raise RuntimeError(f"Category {category_name!r} not found. Available: {opts}")

    # Walk weeks to find the group_id from the dameJornada response.
    # We try recent weeks until we see the group header; basketaraba returns
    # all groups of the category for a given week.
    group_id = _find_group_id(client, category_id, target)
    return GroupRef(category_name=category_name, category_id=category_id,
                    group_name=target, group_id=group_id)


def _find_group_id(client: Client, category_id: str, group_name_norm: str) -> str:
    """Scan a few past weeks until we find the group header and its id."""
    # Try the current week first, then walk back up to ~30 weeks (covers a season).
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    for offset in range(0, 30):
        week = monday - timedelta(weeks=offset)
        html = client.get(
            f"{BASE}/ajax/dameJornada.php?live=1&week={week.isoformat()}&categoria={category_id}"
        )
        gid = _extract_group_id(html, group_name_norm)
        if gid:
            return gid
    raise RuntimeError(f"Could not find group {group_name_norm!r} in any recent jornada")


def fetch_calendar(client: Client, group: GroupRef) -> tuple[str, SeasonCalendar]:
    html = client.get(f"{BASE}/calendario/{group.group_id}")
    return html, _parse_calendar(html, group.group_name)


# ---------------------------------------------------------------------------
# Match-id resolution: walk weekly jornadas to map matchup → partido id
# ---------------------------------------------------------------------------


def fetch_week_jornada(client: Client, category_id: str, monday: date) -> tuple[str, list[tuple[str, JornadaMatchEntry]]]:
    """Returns (raw_html, [(group_name, entry)]) for a Monday."""
    html = client.get(
        f"{BASE}/ajax/dameJornada.php?live=1&week={monday.isoformat()}&categoria={category_id}"
    )
    return html, _parse_week_jornada(html)


def fetch_match(client: Client, partido_id: str) -> tuple[str, MatchDetail]:
    html = client.get(f"{BASE}/ajax/damePartido.php?partido={partido_id}")
    return html, _parse_match(html, partido_id)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def crawl(group_name: str, out_root: Path, sleep: float, force: bool) -> dict:
    started_at = time.monotonic()
    client = Client(sleep=sleep)
    cached_calendar_reads = 0
    cached_week_reads = 0
    cached_match_reads = 0
    failed_match_fetches = 0

    log.info("Resolving group: %s", group_name)
    group = resolve_group(client, group_name)
    log.info("→ category_id=%s group_id=%s", group.category_id, group.group_id)
    resolved_at = time.monotonic()

    out_dir = out_root / _slugify(group.group_name)
    raw_dir = out_dir / "raw"
    matches_dir = out_dir / "matches"

    # Calendar
    calendar_raw_path = raw_dir / "calendario.html"
    if calendar_raw_path.exists() and not force:
        cached_calendar_reads += 1
        cal_html = calendar_raw_path.read_text(encoding="utf-8")
        calendar = _parse_calendar(cal_html, group.group_name)
    else:
        cal_html, calendar = fetch_calendar(client, group)
        _write_raw(calendar_raw_path, cal_html)
    log.info("Calendar: %d teams, %d matches across %d jornadas",
             len(calendar.teams), len(calendar.matches),
             len({m.jornada for m in calendar.matches}))
    calendar_loaded_at = time.monotonic()

    # Determine the set of Mondays we need to query
    mondays_by_jornada: dict[int, date] = {}
    for cm in calendar.matches:
        mondays_by_jornada.setdefault(cm.jornada, _ddmmyyyy_to_monday(cm.jornada_date))

    # Build team-id lookups so we can map every entry (weekly or calendar-only)
    # to canonical team_ids.
    name_to_team_id = {n: tid for tid, n in calendar.teams.items()}
    logo_to_team_id: dict[str, str] = {}
    for cm in calendar.matches:
        if cm.home_logo and cm.home_team_id:
            logo_to_team_id.setdefault(cm.home_logo, cm.home_team_id)
        if cm.away_logo and cm.away_team_id:
            logo_to_team_id.setdefault(cm.away_logo, cm.away_team_id)

    def _resolve(name: str | None, logo: str | None) -> str:
        if name and name in name_to_team_id:
            return name_to_team_id[name]
        if logo and logo in logo_to_team_id:
            return logo_to_team_id[logo]
        return ""

    # Walk weekly jornadas, collecting every match seen.
    raw_entries: list[dict] = []
    seen_ids: set[str] = set()
    target_group = _norm(group.group_name)
    for jornada, monday in sorted(mondays_by_jornada.items()):
        raw_path = raw_dir / f"jornada_{monday.isoformat()}.html"
        if raw_path.exists() and not force:
            cached_week_reads += 1
            html = raw_path.read_text(encoding="utf-8")
        else:
            html, _ = fetch_week_jornada(client, group.category_id, monday)
            _write_raw(raw_path, html)
        entries = _parse_week_jornada(html)
        in_group = [e for g, e in entries if g == target_group]
        log.info("Jornada %d (%s): %d matches", jornada, monday.isoformat(), len(in_group))
        for entry in in_group:
            home_team_id = _resolve(entry.home_team, entry.home_logo)
            away_team_id = _resolve(entry.away_team, entry.away_logo)
            raw_entries.append({
                "jornada": jornada,
                "monday": monday.isoformat(),
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "source": "jornada",
                **_to_dict(entry),
            })
            if entry.partido_id:
                seen_ids.add(entry.partido_id)

    # Fold in calendar entries — covers walkovers / forfeits with no acta digital
    # that the weekly AJAX doesn't surface.
    for cm in calendar.matches:
        monday = mondays_by_jornada.get(cm.jornada)
        status = "FINALIZADO" if (cm.home_score is not None and cm.away_score is not None) else "SIN EMPEZAR"
        raw_entries.append({
            "jornada": cm.jornada,
            "monday": monday.isoformat() if monday else None,
            "home_team_id": cm.home_team_id,
            "away_team_id": cm.away_team_id,
            "source": "calendar",
            "partido_id": "",
            "home_team": cm.home_team,
            "away_team": cm.away_team,
            "home_score": cm.home_score,
            "away_score": cm.away_score,
            "status": status,
            "venue": None,
            "starts_at": None,
            "home_logo": cm.home_logo,
            "away_logo": cm.away_logo,
        })

    # Dedup by ordered (home_team_id, away_team_id) — in a double round-robin
    # each ordered pair plays exactly once. A postponed game shows up in two
    # different jornada blocks; merge them, preferring the entry with the
    # partido_id (i.e. the played one with the digital acta).
    def _entry_score(e: dict) -> tuple:
        return (
            bool(e.get("partido_id")),                  # entries with acta win
            e.get("source") == "jornada",               # weekly AJAX preferred over calendar
            e.get("status") == "FINALIZADO",            # finalized over pending/suspended
            e.get("home_score") is not None,            # has a result
        )

    bucket: dict[tuple[str, str], dict] = {}
    skipped = 0
    for e in raw_entries:
        key = (e.get("home_team_id") or "", e.get("away_team_id") or "")
        if not (key[0] and key[1]):
            skipped += 1
            continue
        prev = bucket.get(key)
        if prev is None or _entry_score(e) > _entry_score(prev):
            bucket[key] = e
    if skipped:
        log.warning("Skipped %d entries with unresolved team_ids", skipped)
    cal_added = sum(1 for e in bucket.values() if e.get("source") == "calendar")
    log.info("Index: %d matches (%d from calendar-only, no acta)", len(bucket), cal_added)
    index_built_at = time.monotonic()

    jornada_entries = sorted(bucket.values(),
                             key=lambda e: (e["jornada"], e.get("monday") or "", e.get("starts_at") or ""))

    _write_json(out_dir / "matches.json", {
        "group": _to_dict(group),
        "matches": jornada_entries,
    })

    # Group metadata
    _write_json(out_dir / "group.json", {
        "group": _to_dict(group),
        "teams": calendar.teams,
        "season_jornadas": {str(j): m.isoformat() for j, m in sorted(mondays_by_jornada.items())},
    })

    # Per-match detail
    log.info("Fetching %d matches with partido ids…", len(seen_ids))
    for i, pid in enumerate(sorted(seen_ids), 1):
        raw_path = raw_dir / f"partido_{pid}.html"
        json_path = matches_dir / f"{pid}.json"
        if json_path.exists() and not force:
            cached_match_reads += 1
            log.info("[%d/%d] %s (cached)", i, len(seen_ids), pid)
            continue
        log.info("[%d/%d] %s", i, len(seen_ids), pid)
        try:
            html, detail = fetch_match(client, pid)
        except Exception as exc:
            failed_match_fetches += 1
            log.exception("Failed to fetch partido %s: %s", pid, exc)
            continue
        _write_raw(raw_path, html)
        _write_json(json_path, detail)

    finished_at = time.monotonic()
    log.info(
        "Requests summary: network_requests=%d cached_calendar=%d cached_weeks=%d cached_matches=%d failed_matches=%d calendar_only=%d timings_s(resolve=%.3f calendar=%.3f index=%.3f detail=%.3f total=%.3f)",
        client.request_count,
        cached_calendar_reads,
        cached_week_reads,
        cached_match_reads,
        failed_match_fetches,
        cal_added,
        resolved_at - started_at,
        calendar_loaded_at - resolved_at,
        index_built_at - calendar_loaded_at,
        finished_at - index_built_at,
        finished_at - started_at,
    )
    metrics = {
        "engine": "requests",
        "network_requests": client.request_count,
        "cached_calendar": cached_calendar_reads,
        "cached_weeks": cached_week_reads,
        "cached_matches": cached_match_reads,
        "failed_matches": failed_match_fetches,
        "calendar_only": cal_added,
        "timings_s": {
            "resolve": round(resolved_at - started_at, 3),
            "calendar": round(calendar_loaded_at - resolved_at, 3),
            "index": round(index_built_at - calendar_loaded_at, 3),
            "detail": round(finished_at - index_built_at, 3),
            "total": round(finished_at - started_at, 3),
        },
    }
    _emit_metrics(metrics)
    log.info("Done. Output in %s", out_dir)
    return metrics


def _default_engine() -> str:
    configured = os.environ.get("BASKETARABA_DEFAULT_ENGINE", "scrapy").strip().lower()
    return configured if configured in {"requests", "scrapy"} else "scrapy"


def _run_engine_subprocess(engine: str, group: str, out: Path, sleep: float, force: bool, verbose: bool) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), group, "--engine", engine, "--out", str(out), "--sleep", str(sleep)]
    if force:
        command.append("--force")
    if verbose:
        command.append("--verbose")

    env = os.environ.copy()
    env["BASKETARABA_EMIT_METRICS_JSON"] = "1"
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=True)
    lines = completed.stdout.splitlines() + completed.stderr.splitlines()
    metrics_line = next((line for line in lines if line.startswith("METRICS_JSON: ")), None)
    if not metrics_line:
        raise RuntimeError(f"No metrics line found for engine {engine!r}")
    return json.loads(metrics_line.split(": ", 1)[1])


def compare_engines(group_name: str, out_root: Path, sleep: float, force: bool, verbose: bool, metrics_out: Path | None) -> int:
    requests_metrics = _run_engine_subprocess("requests", group_name, out_root, sleep, force, verbose)
    scrapy_metrics = _run_engine_subprocess("scrapy", group_name, out_root, sleep, force, verbose)
    requests_timings = requests_metrics["timings_s"]
    scrapy_timings = scrapy_metrics["timings_s"]
    deltas = {
        "resolve_calendar_total_s": round(
            scrapy_timings["resolve_and_calendar"] - (requests_timings["resolve"] + requests_timings["calendar"]),
            3,
        ),
        "index_s": round(scrapy_timings["index"] - requests_timings["index"], 3),
        "detail_s": round(scrapy_timings["detail"] - requests_timings["detail"], 3),
        "total_s": round(scrapy_timings["total"] - requests_timings["total"], 3),
        "cached_matches": scrapy_metrics["cached_matches"] - requests_metrics["cached_matches"],
    }

    print("Engine comparison:")
    print(
        "- requests: "
        f"network_requests={requests_metrics['network_requests']} cached_matches={requests_metrics['cached_matches']} "
        f"calendar_only={requests_metrics['calendar_only']} total_s={requests_metrics['timings_s']['total']}"
    )
    print(
        "- scrapy: "
        f"scheduled_network_requests={scrapy_metrics['scheduled_network_requests']} cached_matches={scrapy_metrics['cached_matches']} "
        f"calendar_only={scrapy_metrics['calendar_only']} retries={scrapy_metrics['retries']} total_s={scrapy_metrics['timings_s']['total']}"
    )
    print(
        "- delta(resolve+calendar_s scrapy-requests): "
        f"{deltas['resolve_calendar_total_s']}"
    )
    print(
        "- delta(index_s scrapy-requests): "
        f"{deltas['index_s']}"
    )
    print(
        "- delta(detail_s scrapy-requests): "
        f"{deltas['detail_s']}"
    )
    print(
        "- delta(total_s scrapy-requests): "
        f"{deltas['total_s']}"
    )
    print(
        "- delta(cached_matches scrapy-requests): "
        f"{deltas['cached_matches']}"
    )
    if metrics_out is not None:
        _write_metrics_file(
            metrics_out,
            {
                "requests": requests_metrics,
                "scrapy": scrapy_metrics,
                "deltas": deltas,
            },
        )
    metrics_history_dir = os.environ.get("BASKETARABA_METRICS_HISTORY_DIR")
    if metrics_history_dir:
        snapshot_path = _write_metrics_snapshot(
            Path(metrics_history_dir),
            group_name,
            "compare",
            {
                "requests": requests_metrics,
                "scrapy": scrapy_metrics,
                "deltas": deltas,
            },
        )
        log.info("Metrics snapshot written to %s", snapshot_path)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("group", help="Group name, e.g. 'SENIOR MASCULINA 3ª-GRUPO A'")
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root directory (default: ./data)")
    p.add_argument("--engine", choices=("requests", "scrapy"), default=_default_engine(),
                   help="Crawler engine to use (default: BASKETARABA_DEFAULT_ENGINE or scrapy)")
    p.add_argument("--compare-engines", action="store_true",
                   help="Run both engines sequentially and print a compact comparison")
    p.add_argument("--metrics-out", type=Path, help="Write run metrics to a JSON file")
    p.add_argument("--metrics-history-dir", type=Path,
                   help="Write timestamped metrics snapshots under the given root directory")
    p.add_argument("--sleep", type=float, default=0.4, help="Seconds between HTTP requests (default: 0.4)")
    p.add_argument("--force", action="store_true", help="Re-download even if cached files exist")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Selected crawler engine: %s", args.engine)

    if args.metrics_history_dir is not None:
        os.environ["BASKETARABA_METRICS_HISTORY_DIR"] = str(args.metrics_history_dir)
    else:
        os.environ.pop("BASKETARABA_METRICS_HISTORY_DIR", None)

    if args.compare_engines:
        return compare_engines(args.group, args.out, args.sleep, args.force, args.verbose, args.metrics_out)

    if args.engine == "scrapy":
        from scraper.run import main as scrapy_main

        delegated_argv = [args.group, "--out", str(args.out), "--sleep", str(args.sleep)]
        if args.force:
            delegated_argv.append("--force")
        if args.verbose:
            delegated_argv.append("--verbose")
        if args.metrics_out is not None:
            delegated_argv.extend(["--metrics-out", str(args.metrics_out)])
        if args.metrics_history_dir is not None:
            delegated_argv.extend(["--metrics-history-dir", str(args.metrics_history_dir)])
        return scrapy_main(delegated_argv)

    metrics = crawl(args.group, args.out, args.sleep, args.force)
    if args.metrics_out is not None:
        _write_metrics_file(args.metrics_out, metrics)
    if args.metrics_history_dir is not None:
        snapshot_path = _write_metrics_snapshot(args.metrics_history_dir, args.group, "requests", metrics)
        log.info("Metrics snapshot written to %s", snapshot_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

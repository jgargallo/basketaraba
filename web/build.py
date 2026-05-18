#!/usr/bin/env python3
"""
Build the static stats website from one or more database.json files.

Layout produced under --out (default web/dist):
    index.html          single-page app shell
    styles.css
    app.js
    data/
        index.json                          group directory
        <group_slug>/
            league.json
            teams/<team_id>.json
            players/<player_id>.json
            games/<game_id>.json
    assets/
        logos/<group_slug>/                 team logos

Usage:
    python web/build.py data/senior-masculina-3a-grupo-a/database.json
    python web/build.py data/a/database.json data/b/database.json
    python web/build.py --all
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen, Request


BASKET_LOGOS_URL = "https://basketaraba.com/actadigital/images/logos/"
MIN_FT_ATT_FOR_PCT_RANK = 20
MIN_GAMES_FOR_LEADERS = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _team_logo_url(team: dict) -> str | None:
    local_url = team.get("logo_url")
    if local_url:
        return local_url
    fn = team.get("logo_filename")
    if not fn:
        return None
    return BASKET_LOGOS_URL + quote(fn)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only.lower()).strip("-")
    return re.sub(r"-+", "-", cleaned)


def _download_logo(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        destination.write_bytes(response.read())


_LOGO_NETWORK_ERRORS = (URLError, socket.error, socket.timeout, ConnectionError, OSError)


def _materialize_local_logos(db: dict, out_dir: Path, season: str | None = None) -> None:
    group_name = db.get("group", {}).get("group_name") or "group"
    slug = _slugify(group_name)
    if season:
        logos_dir = out_dir / "assets" / "logos" / season / slug
        logo_url_prefix = "/".join(["assets", "logos", season, slug])
    else:
        logos_dir = out_dir / "assets" / "logos" / slug
        logo_url_prefix = "/".join(["assets", "logos", slug])
    logos_dir.mkdir(parents=True, exist_ok=True)

    for team in db.get("teams", []):
        remote_url = _team_logo_url(team)
        logo_filename = team.get("logo_filename")
        if not remote_url or not logo_filename:
            team["logo_url"] = None
            continue

        destination = logos_dir / logo_filename
        try:
            _download_logo(remote_url, destination)
        except _LOGO_NETWORK_ERRORS:
            team["logo_url"] = None
            continue
        team["logo_url"] = "/".join([logo_url_prefix, quote(logo_filename)])


def _index_by(items: list[dict], key: str) -> dict[str, dict]:
    return {it[key]: it for it in items}


def _safe_div(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


# ---------------------------------------------------------------------------
# View-model builders
# ---------------------------------------------------------------------------

def build_league(db: dict) -> dict:
    """Top-level overview: group, teams w/ logos, standings, league leaders."""
    teams = [{**t, "logo_url": _team_logo_url(t)} for t in db["teams"]]
    teams_idx = _index_by(teams, "id")
    players_idx = _index_by(db["players"], "id")

    # Standings — team_season_stats is already sorted by win_pct desc, diff desc
    standings = []
    for rank, ts in enumerate(db["team_season_stats"], start=1):
        t = teams_idx.get(ts["team_id"], {"id": ts["team_id"], "name": ts["team_id"]})
        standings.append({
            "rank": rank,
            "team_id": ts["team_id"],
            "team_name": t["name"],
            "logo_url": t.get("logo_url"),
            "games_played": ts["games_played"],
            "wins": ts["wins"],
            "losses": ts["losses"],
            "draws": ts["draws"],
            "win_pct": ts["win_pct"],
            "points_for": ts["points_for"],
            "points_against": ts["points_against"],
            "point_diff": ts["point_diff"],
            "avg_for": ts["avg_points_for"],
            "avg_against": ts["avg_points_against"],
        })

    # League leaders
    def player_card(ps: dict, primary_label: str, primary_value, extra: dict) -> dict:
        p = players_idx.get(ps["player_id"], {})
        team = teams_idx.get(ps["team_id"], {})
        return {
            "player_id": ps["player_id"],
            "player_name": p.get("name") or ps["player_id"],
            "team_id": ps["team_id"],
            "team_name": team.get("name", ""),
            "logo_url": team.get("logo_url"),
            "games_played": ps["games_played"],
            "primary": {"label": primary_label, "value": primary_value},
            **extra,
        }

    eligible = [ps for ps in db["player_season_stats"] if ps["games_played"] >= MIN_GAMES_FOR_LEADERS]

    top_scorers = sorted(eligible, key=lambda r: -r["averages"]["pts"])[:15]
    top_3pt = sorted(eligible, key=lambda r: -r["averages"]["t3"])[:15]
    top_2pt = sorted(eligible, key=lambda r: -r["averages"]["t2"])[:15]
    top_ft = [r for r in eligible if r["totals"]["tl_att"] >= MIN_FT_ATT_FOR_PCT_RANK]
    top_ft = sorted(top_ft, key=lambda r: -(r["ft_pct"] or 0))[:15]
    top_volume_scorers = sorted(eligible, key=lambda r: -r["totals"]["pts"])[:15]
    most_disciplined = sorted(eligible, key=lambda r: r["averages"]["fp"])[:15]
    most_fp_personal = sorted(eligible, key=lambda r: -r["averages"].get("fp_personal", 0))[:15]
    most_fp_technical = sorted(
        [r for r in db["player_season_stats"] if r["totals"].get("fp_technical", 0) > 0],
        key=lambda r: -r["totals"]["fp_technical"],
    )[:15]
    most_fp_anti = sorted(
        [r for r in db["player_season_stats"] if r["totals"].get("fp_anti", 0) > 0],
        key=lambda r: -r["totals"]["fp_anti"],
    )[:15]

    leaders = {
        "ppg": [player_card(r, "PPG", r["averages"]["pts"], {"secondary": [
            ("PTS total", r["totals"]["pts"]), ("GP", r["games_played"])
        ]}) for r in top_scorers],
        "tpg_3": [player_card(r, "3PG", r["averages"]["t3"], {"secondary": [
            ("3 hechos", r["totals"]["t3"]), ("GP", r["games_played"])
        ]}) for r in top_3pt],
        "tpg_2": [player_card(r, "2PG", r["averages"]["t2"], {"secondary": [
            ("2 hechos", r["totals"]["t2"]), ("GP", r["games_played"])
        ]}) for r in top_2pt],
        "ft_pct": [player_card(r, "TL%", round((r["ft_pct"] or 0) * 100, 1), {"secondary": [
            ("TL", f"{r['totals']['tl_made']}/{r['totals']['tl_att']}"),
            ("GP", r["games_played"]),
        ]}) for r in top_ft],
        "pts_total": [player_card(r, "PTS", r["totals"]["pts"], {"secondary": [
            ("PPG", r["averages"]["pts"]), ("GP", r["games_played"])
        ]}) for r in top_volume_scorers],
        "low_fp": [player_card(r, "FP/GP", r["averages"]["fp"], {"secondary": [
            ("Faltas total", r["totals"]["fp"]), ("GP", r["games_played"])
        ]}) for r in most_disciplined],
        "fp_personal_pg": [player_card(r, "FP/GP", r["averages"]["fp_personal"], {"secondary": [
            ("FP total", r["totals"]["fp_personal"]), ("GP", r["games_played"])
        ]}) for r in most_fp_personal],
        "fp_technical": [player_card(r, "Téc.", r["totals"]["fp_technical"], {"secondary": [
            ("GP", r["games_played"])
        ]}) for r in most_fp_technical],
        "fp_anti": [player_card(r, "Anti.", r["totals"]["fp_anti"], {"secondary": [
            ("GP", r["games_played"])
        ]}) for r in most_fp_anti],
    }

    # Schedule: lightweight game list (no log)
    games_lite = []
    for g in db["games"]:
        games_lite.append({
            "id": g["id"],
            "jornada": g["jornada"],
            "date": g["date"],
            "status": g["status"],
            "home_team_id": g["home_team_id"],
            "away_team_id": g["away_team_id"],
            "home_score": g["home_score"],
            "away_score": g["away_score"],
            "winner": g["winner"],
            "has_box_score": g.get("has_box_score", bool(g["id"])),
        })
    games_lite.sort(key=lambda g: (g["jornada"], g["date"] or ""))

    return {
        "group": db["group"],
        "teams": teams,
        "standings": standings,
        "leaders": leaders,
        "games": games_lite,
        "totals": {
            "games": len(games_lite),
            "completed": sum(1 for g in games_lite if g["status"] == "FINALIZADO"),
            "players": len(db["players"]),
            "teams": len(teams),
        },
    }


def build_team_views(db: dict) -> dict[str, dict]:
    """Per-team JSON: roster with season stats, top performers, games, per-quarter trend."""
    teams = [{**t, "logo_url": _team_logo_url(t)} for t in db["teams"]]
    teams_idx = _index_by(teams, "id")
    players_idx = _index_by(db["players"], "id")
    player_season_idx = _index_by(db["player_season_stats"], "player_id")
    pgs_by_player: dict[str, list[dict]] = defaultdict(list)
    for r in db["player_game_stats"]:
        pgs_by_player[r["player_id"]].append(r)

    standing_by_team = {ts["team_id"]: ts for ts in db["team_season_stats"]}
    rank_by_team = {ts["team_id"]: i + 1 for i, ts in enumerate(db["team_season_stats"])}

    games_by_team: dict[str, list[dict]] = defaultdict(list)
    for g in db["games"]:
        for tid in (g["home_team_id"], g["away_team_id"]):
            if tid:
                games_by_team[tid].append(g)

    out: dict[str, dict] = {}
    for team in teams:
        tid = team["id"]
        ts = standing_by_team.get(tid)
        # Roster: every player whose team_id matches
        roster = []
        for p in db["players"]:
            if p["team_id"] != tid:
                continue
            ps = player_season_idx.get(p["id"])
            games_played_list = [g for g in pgs_by_player.get(p["id"], []) if g["played"]]
            highs_game = None
            if games_played_list:
                top = max(games_played_list, key=lambda g: g["pts"])
                highs_game = {"game_id": top["game_id"], "pts": top["pts"]}
            roster.append({
                "id": p["id"],
                "name": p["name"],
                "dorsals": p["dorsals"],
                "games_played": ps["games_played"] if ps else 0,
                "totals": ps["totals"] if ps else None,
                "averages": ps["averages"] if ps else None,
                "ft_pct": ps["ft_pct"] if ps else None,
                "highs": ps["highs"] if ps else None,
                "per_quarter_averages": ps["per_quarter_averages"] if ps else None,
                "top_game": highs_game,
            })
        roster.sort(key=lambda r: -(r["averages"]["pts"] if r["averages"] else -1))

        # Team's games timeline
        games = []
        for g in sorted(games_by_team.get(tid, []), key=lambda g: (g["jornada"], g["date"] or "")):
            is_home = g["home_team_id"] == tid
            opp_id = g["away_team_id"] if is_home else g["home_team_id"]
            for_pts = g["home_score"] if is_home else g["away_score"]
            against_pts = g["away_score"] if is_home else g["home_score"]
            result = None
            if g["status"] == "FINALIZADO" and for_pts is not None and against_pts is not None:
                result = "W" if for_pts > against_pts else ("L" if for_pts < against_pts else "D")
            games.append({
                "id": g["id"],
                "jornada": g["jornada"],
                "date": g["date"],
                "status": g["status"],
                "is_home": is_home,
                "opponent_id": opp_id,
                "opponent_name": teams_idx.get(opp_id, {}).get("name") if opp_id else None,
                "opponent_logo": teams_idx.get(opp_id, {}).get("logo_url") if opp_id else None,
                "for_pts": for_pts,
                "against_pts": against_pts,
                "result": result,
                "quarters_for": [q[0] if is_home else q[1] for q in (g.get("quarters") or [])],
                "quarters_against": [q[1] if is_home else q[0] for q in (g.get("quarters") or [])],
                "has_box_score": g.get("has_box_score", bool(g["id"])),
            })

        # Top performers cards
        played_roster = [r for r in roster if r["averages"]]
        top_scorer = max(played_roster, key=lambda r: r["averages"]["pts"], default=None)
        top_3pt = max(played_roster, key=lambda r: r["averages"]["t3"], default=None)
        top_2pt = max(played_roster, key=lambda r: r["averages"]["t2"], default=None)
        ft_eligible = [r for r in played_roster if r["totals"]["tl_att"] >= 10]
        top_ft = max(ft_eligible, key=lambda r: (r["ft_pct"] or 0), default=None)
        clean_eligible = [r for r in played_roster if r["games_played"] >= 5]
        most_disciplined = min(clean_eligible, key=lambda r: r["averages"]["fp"], default=None)
        most_fouled = max(played_roster, key=lambda r: r["averages"]["fp"], default=None)
        best_single = max(played_roster, key=lambda r: r["highs"]["pts"] if r["highs"] else 0, default=None)

        out[tid] = {
            "team": team,
            "rank": rank_by_team.get(tid),
            "season": ts,
            "roster": roster,
            "games": games,
            "highlights": {
                "top_scorer": _player_highlight(top_scorer, "PPG", lambda r: r["averages"]["pts"]),
                "top_3pt": _player_highlight(top_3pt, "3 por partido", lambda r: r["averages"]["t3"]),
                "top_2pt": _player_highlight(top_2pt, "2 por partido", lambda r: r["averages"]["t2"]),
                "top_ft": _player_highlight(top_ft, "TL%", lambda r: round((r["ft_pct"] or 0) * 100, 1)),
                "most_disciplined": _player_highlight(most_disciplined, "Faltas/GP", lambda r: r["averages"]["fp"]),
                "most_fouled": _player_highlight(most_fouled, "Faltas/GP", lambda r: r["averages"]["fp"]),
                "best_single_game": _player_highlight(best_single, "Mejor partido (PTS)",
                                                     lambda r: r["highs"]["pts"] if r["highs"] else 0),
            },
        }
    return out


def _player_highlight(r: dict | None, label: str, value_fn) -> dict | None:
    if not r:
        return None
    return {
        "player_id": r["id"],
        "player_name": r["name"],
        "dorsals": r["dorsals"],
        "games_played": r["games_played"],
        "label": label,
        "value": value_fn(r),
    }


def build_player_views(db: dict) -> dict[str, dict]:
    teams = [{**t, "logo_url": _team_logo_url(t)} for t in db["teams"]]
    teams_idx = _index_by(teams, "id")
    games_idx = _index_by(db["games"], "id")
    player_season_idx = _index_by(db["player_season_stats"], "player_id")
    pgs_by_player: dict[str, list[dict]] = defaultdict(list)
    for r in db["player_game_stats"]:
        pgs_by_player[r["player_id"]].append(r)

    out: dict[str, dict] = {}
    for p in db["players"]:
        ps = player_season_idx.get(p["id"])
        my_games = sorted(pgs_by_player.get(p["id"], []), key=lambda g: (
            games_idx[g["game_id"]]["jornada"] if g["game_id"] in games_idx else 99,
            games_idx[g["game_id"]]["date"] or "" if g["game_id"] in games_idx else "",
        ))
        rows = []
        for r in my_games:
            g = games_idx.get(r["game_id"]) or {}
            is_home = g.get("home_team_id") == r["team_id"]
            opp_id = g.get("away_team_id") if is_home else g.get("home_team_id")
            opp_team = teams_idx.get(opp_id, {})
            rows.append({
                "game_id": r["game_id"],
                "jornada": g.get("jornada"),
                "date": g.get("date"),
                "is_home": is_home,
                "opponent_id": opp_id,
                "opponent_name": opp_team.get("name"),
                "opponent_logo": opp_team.get("logo_url"),
                "dorsal": r["dorsal"],
                "pts": r["pts"],
                "t2": r["t2"],
                "t3": r["t3"],
                "tl_made": r["tl_made"],
                "tl_att": r["tl_att"],
                "fp": r["fp"],
                "ft_pct": r["ft_pct"],
                "played": r["played"],
                "by_quarter": r["by_quarter"],
                "team_for": g.get("home_score") if is_home else g.get("away_score"),
                "team_against": g.get("away_score") if is_home else g.get("home_score"),
            })

        team_meta = teams_idx.get(p["team_id"], {})
        out[p["id"]] = {
            "player": {**p, "team_logo": team_meta.get("logo_url"), "team_name": team_meta.get("name")},
            "season": ps,
            "games": rows,
        }
    return out


def build_game_views(db: dict) -> dict[str, dict]:
    teams = [{**t, "logo_url": _team_logo_url(t)} for t in db["teams"]]
    teams_idx = _index_by(teams, "id")
    players_idx = _index_by(db["players"], "id")

    pgs_by_game: dict[str, list[dict]] = defaultdict(list)
    for r in db["player_game_stats"]:
        if r["game_id"]:
            pgs_by_game[r["game_id"]].append(r)
    log_by_game: dict[str, list[dict]] = defaultdict(list)
    for e in db["log_events"]:
        if e["game_id"]:
            log_by_game[e["game_id"]].append(e)

    out: dict[str, dict] = {}
    for g in db["games"]:
        gid = g["id"]
        if not gid:
            continue
        home = teams_idx.get(g["home_team_id"], {})
        away = teams_idx.get(g["away_team_id"], {})

        def _team_block(team_id: str, team: dict) -> dict:
            rows = []
            for r in sorted(pgs_by_game.get(gid, []),
                            key=lambda r: -r["pts"] if r["team_id"] == team_id else 999):
                if r["team_id"] != team_id:
                    continue
                p = players_idx.get(r["player_id"], {})
                rows.append({
                    "player_id": r["player_id"],
                    "name": p.get("name") or r["player_id"],
                    "dorsal": r["dorsal"],
                    "pts": r["pts"], "t2": r["t2"], "t3": r["t3"],
                    "tl_made": r["tl_made"], "tl_att": r["tl_att"], "fp": r["fp"],
                    "ft_pct": r["ft_pct"], "played": r["played"],
                    "by_quarter": r["by_quarter"],
                })
            return {
                "team_id": team_id,
                "team_name": team.get("name"),
                "logo_url": team.get("logo_url"),
                "players": rows,
            }

        log = []
        for e in log_by_game.get(gid, []):
            p = players_idx.get(e.get("player_id") or "")
            log.append({
                **e,
                "player_name": p["name"] if p else e.get("player_name"),
            })

        out[gid] = {
            "game": {
                "id": gid,
                "jornada": g["jornada"],
                "date": g["date"],
                "venue": g.get("venue"),
                "status": g["status"],
                "home_score": g["home_score"],
                "away_score": g["away_score"],
                "winner": g["winner"],
                "quarters": g.get("quarters") or [],
            },
            "home": _team_block(g["home_team_id"], home),
            "away": _team_block(g["away_team_id"], away),
            "log": log,
        }
    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _season_from_path(db_path: Path) -> str | None:
    """Extract season label from data/<season>/<group>/database.json, or None.

    Works with both absolute and relative paths; inspects the directory two
    levels above the file (i.e. db_path.parent.parent.name).
    """
    candidate = db_path.resolve().parent.parent.name
    if re.match(r"^\d{4}-\d{2}$", candidate):
        return candidate
    return None


def _build_group(db: dict, out_dir: Path, season: str | None = None) -> dict:
    """Build one group's static subtree. Returns metadata for index.json."""
    group_slug = _slugify(db["group"]["group_name"])
    if season:
        group_dir = out_dir / "data" / season / group_slug
    else:
        group_dir = out_dir / "data" / group_slug
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / "teams").mkdir(exist_ok=True)
    (group_dir / "players").mkdir(exist_ok=True)
    (group_dir / "games").mkdir(exist_ok=True)

    _materialize_local_logos(db, out_dir, season=season)

    league = build_league(db)
    (group_dir / "league.json").write_text(
        json.dumps(league, ensure_ascii=False), encoding="utf-8"
    )

    team_views = build_team_views(db)
    for tid, view in team_views.items():
        (group_dir / "teams" / f"{tid}.json").write_text(
            json.dumps(view, ensure_ascii=False), encoding="utf-8"
        )

    player_views = build_player_views(db)
    for pid, view in player_views.items():
        (group_dir / "players" / f"{pid}.json").write_text(
            json.dumps(view, ensure_ascii=False), encoding="utf-8"
        )

    game_views = build_game_views(db)
    for gid, view in game_views.items():
        (group_dir / "games" / f"{gid}.json").write_text(
            json.dumps(view, ensure_ascii=False), encoding="utf-8"
        )

    completed = sum(1 for g in db["games"] if g["status"] == "FINALIZADO")
    return {
        "season": season,
        "id": group_slug,
        "name": db["group"]["group_name"],
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "teams": len(db["teams"]),
            "players": len(db["players"]),
            "games": len(db["games"]),
            "completed_games": completed,
        },
    }


def _build_index(groups_meta: list[dict], out_dir: Path) -> None:
    # Build season-aware structure: {current_season, seasons: {<season>: {label, groups: [...]}}}
    seasons: dict[str, list[dict]] = {}
    for meta in groups_meta:
        s = meta.get("season") or "unknown"
        seasons.setdefault(s, []).append({
            "slug": meta["id"],
            "display_name": meta["name"],
        })

    sorted_seasons = sorted(seasons.keys(), reverse=True)
    current_season = sorted_seasons[0] if sorted_seasons else "unknown"

    index = {
        "current_season": current_season,
        "seasons": {
            s: {
                "label": f"Temporada {s}",
                "groups": seasons[s],
            }
            for s in sorted_seasons
        },
        # Legacy flat list for backward compatibility.
        "groups": groups_meta,
    }
    (out_dir / "data" / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _copy_static_files(src_dir: Path, out_dir: Path) -> None:
    for fname in ("index.html", "styles.css", "app.js"):
        src = src_dir / fname
        if not src.exists():
            raise FileNotFoundError(f"Missing frontend source: {src}")
        shutil.copy2(src, out_dir / fname)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("databases", nargs="*", type=Path, metavar="database",
                   help="Path(s) to database.json file(s) produced by stats.py")
    p.add_argument("--all", action="store_true", dest="all_groups",
                   help="Autodiscover all data/*/database.json files in the current directory")
    p.add_argument("--out", type=Path, default=Path("web/dist"),
                   help="Output directory (default: web/dist)")
    p.add_argument("--src", type=Path, default=Path("web/src"),
                   help="Frontend source directory (default: web/src)")
    args = p.parse_args()

    if args.all_groups:
        # Support both data/<season>/<group>/database.json and legacy data/<group>/database.json
        db_paths = sorted(Path("data").glob("*/*/database.json"))
        if not db_paths:
            # Fall back to legacy flat layout
            db_paths = sorted(Path("data").glob("*/database.json"))
        if not db_paths:
            print("No data/*/database.json or data/*/*/database.json files found.", file=sys.stderr)
            return 1
    else:
        db_paths = args.databases

    if not db_paths:
        p.error("Provide at least one database.json path or use --all")

    missing = [db for db in db_paths if not db.exists()]
    if missing:
        for m in missing:
            print(f"Not found: {m}", file=sys.stderr)
        return 1

    # Clean output dir and create skeleton once
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    (args.out / "data").mkdir()

    groups_meta = []
    for db_path in db_paths:
        season = _season_from_path(db_path)
        db = json.loads(db_path.read_text(encoding="utf-8"))
        meta = _build_group(db, args.out, season=season)
        groups_meta.append(meta)
        season_label = f"{season}/" if season else ""
        print(
            f"  [{season_label}{meta['id']}] "
            f"teams={meta['stats']['teams']} "
            f"players={meta['stats']['players']} "
            f"games={meta['stats']['games']} "
            f"completed={meta['stats']['completed_games']}"
        )

    _build_index(groups_meta, args.out)
    _copy_static_files(args.src, args.out)

    print(f"\nBuilt {len(groups_meta)} group(s) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

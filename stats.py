#!/usr/bin/env python3
"""
Build a normalized stats database from the crawler's output.

Reads:    data/<group-slug>/{group.json, matches.json, matches/*.json}
Writes:   data/<group-slug>/database.json

Output is shaped like normalized database tables, ready to load into a relational
DB or feed a website:

    group                       single object
    teams[]                     {id, name}
    players[]                   {id, team_id, team_name, name, dorsals[]}
    games[]                     {id, jornada, date, home_team_id, away_team_id,
                                  home_score, away_score, quarters, winner, status}
    player_game_stats[]         per-player per-game box score + per-quarter breakdown
    log_events[]                normalized play-by-play with foreign keys
    player_season_stats[]       per (player, team) totals + averages + per-quarter avgs
    team_season_stats[]         per team totals + averages + per-quarter avgs

A player is considered to have NOT PLAYED a game if every box-score stat is zero;
those games are excluded from `games_played`, totals and averages so they don't
pollute season aggregates.

Usage:
    python stats.py data/senior-masculina-3a-grupo-a
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def _name_key(name: str) -> str:
    """Canonical identity key for a player name; tolerates casing, punctuation
    and missing commas (the site uses 'Foo, B', 'FOO B.', 'Foo,b' etc.)."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).strip().upper()
    return re.sub(r"\s+", " ", s)


_CANON_NAME = re.compile(r"^[A-ZÁÉÍÓÚÑ][\w'\-áéíóúñü]+,\s+[A-ZÁÉÍÓÚÑ]\.?$")


def _best_display_name(variants: set[str]) -> str:
    """Pick the nicest spelling. Prefer the canonical 'Surname, F[.]' form in
    title case; fall back to whichever has a single comma and mixed case."""
    def score(v: str) -> tuple:
        canonical = bool(_CANON_NAME.match(v))
        mixed_case = v != v.upper() and v != v.lower()
        commas = v.count(",")
        good_comma = commas == 1
        return (canonical, mixed_case, good_comma, -abs(commas - 1), len(v))
    return max(variants, key=score)


def _round(v: float, n: int = 2) -> float:
    return round(v, n)


_FT_TL_RE = re.compile(r"\((\d+)\s*TL\)", re.IGNORECASE)


def classify_event(raw: str, fallback_kind: str) -> tuple[str, dict]:
    """Re-classify a log event so we surface foul types the crawler didn't split out.

    Returns (event_kind, extra_fields).
    """
    text = (raw or "").strip().lower()

    if text.startswith("3 punto"):
        return "made_3", {}
    if text.startswith("2 punto"):
        return "made_2", {}
    if text.startswith("tiro libre"):
        m = re.search(r"(\d+)\s*/\s*(\d+)\s+(metido|fallado)", text)
        if m:
            x, y, status = int(m.group(1)), int(m.group(2)), m.group(3)
            return ("ft_made" if status == "metido" else "ft_missed"), {
                "ft_index": x, "ft_of": y,
            }
        return fallback_kind, {}
    if "tiempo muerto" in text:
        return "timeout", {}
    if "fin de periodo" in text:
        return "period_end", {}

    if "antideportiva" in text:
        kind = "foul_unsportsmanlike"
    elif "descalificante" in text:
        kind = "foul_disqualifying"
    elif "falta técnica" in text or "falta tecnica" in text:
        kind = "foul_technical"
    elif "falta personal" in text or text.startswith("falta"):
        kind = "foul_personal"
    else:
        return fallback_kind, {}

    extra: dict = {}
    m = _FT_TL_RE.search(raw or "")
    if m:
        extra["ft_granted"] = int(m.group(1))
    return kind, extra


def _logo_basename(src: str | None) -> str | None:
    if not src:
        return None
    return src.rsplit("/", 1)[-1]


def _build_logo_lookup(in_dir: Path, teams_by_id: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Parse the cached calendar HTML and return two lookups:
       (logo_filename -> team_id, team_id -> logo_filename)."""
    cal_path = in_dir / "raw" / "calendario.html"
    if not cal_path.exists():
        return {}, {}
    soup = BeautifulSoup(cal_path.read_text(encoding="utf-8"), "lxml")
    by_logo: dict[str, str] = {}
    by_team: dict[str, str] = {}
    for tr in soup.select("tr.partido"):
        classes = tr.get("class") or []
        ids = [c.replace("Equipo", "") for c in classes if c.startswith("Equipo")]
        tds = tr.find_all("td")
        if len(ids) < 2 or len(tds) < 3:
            continue
        home_img = tds[0].find("img")
        away_img = tds[2].find("img")
        if home_img and home_img.get("src"):
            fn = _logo_basename(home_img["src"])
            by_logo[fn] = ids[0]
            by_team.setdefault(ids[0], fn)
        if away_img and away_img.get("src"):
            fn = _logo_basename(away_img["src"])
            by_logo[fn] = ids[1]
            by_team.setdefault(ids[1], fn)
    return by_logo, by_team


def is_played(p: dict) -> bool:
    """A player is considered to have played if any box-score stat is non-zero."""
    return any(p[k] for k in ("pts", "t2", "t3", "tl_made", "tl_att", "fp"))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

@dataclass
class TeamRef:
    id: str
    name: str


def build_database(in_dir: Path) -> dict:
    group_meta = json.loads((in_dir / "group.json").read_text(encoding="utf-8"))
    index = json.loads((in_dir / "matches.json").read_text(encoding="utf-8"))

    teams_by_id: dict[str, str] = dict(group_meta["teams"])  # id -> name
    teams_by_name: dict[str, str] = {v: k for k, v in teams_by_id.items()}
    logo_to_team_id, team_id_to_logo = _build_logo_lookup(in_dir, teams_by_id)

    def resolve_team_id(name: str | None, logo: str | None) -> str | None:
        # Primary: canonical calendar name. Fallback: logo filename (source data
        # sometimes uses alternate sponsor / typo-ed display names in box scores).
        if name and name in teams_by_name:
            return teams_by_name[name]
        if logo:
            tid = logo_to_team_id.get(_logo_basename(logo))
            if tid:
                return tid
        return None

    # registries
    players: dict[tuple[str, str], dict] = {}      # (team_id, name) -> player info
    games: list[dict] = []
    player_game_stats: list[dict] = []
    log_events: list[dict] = []

    def get_player_id(team_id: str, name: str, dorsal: str | None) -> str:
        key = (team_id, _name_key(name))
        if key not in players:
            players[key] = {
                "id": f"{team_id}__{_slug(name)}",
                "team_id": team_id,
                "team_name": teams_by_id.get(team_id, team_id),
                "name": name,
                "name_variants": {name},
                "dorsals": set(),
            }
        else:
            players[key]["name_variants"].add(name)
        if dorsal:
            players[key]["dorsals"].add(dorsal)
        return players[key]["id"]

    # ---- iterate matches in the index ----
    for m in index["matches"]:
        pid = m.get("partido_id") or ""
        if not pid:
            # Walkover / not played yet — still record a games row with what we have,
            # but skip per-player stats and log. Prefer the team_ids the crawler
            # attached; fall back to name+logo resolution.
            home_id = m.get("home_team_id") or resolve_team_id(m.get("home_team"), m.get("home_logo"))
            away_id = m.get("away_team_id") or resolve_team_id(m.get("away_team"), m.get("away_logo"))
            games.append({
                "id": None,
                "jornada": m["jornada"],
                "date": None,
                "venue": m.get("venue"),
                "status": m.get("status"),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_score": m.get("home_score"),
                "away_score": m.get("away_score"),
                "winner": _winner(m.get("home_score"), m.get("away_score"), m.get("status")),
                "quarters": [],
                "has_box_score": False,
            })
            continue

        match_path = in_dir / "matches" / f"{pid}.json"
        if not match_path.exists():
            continue
        detail = json.loads(match_path.read_text(encoding="utf-8"))

        home_team_id = resolve_team_id(detail["home"]["team"], detail["home"].get("logo"))
        away_team_id = resolve_team_id(detail["away"]["team"], detail["away"].get("logo"))

        date_iso = None
        if m.get("starts_at"):
            try:
                date_iso = datetime.strptime(m["starts_at"], "%d/%m/%Y %H:%M").isoformat()
            except ValueError:
                pass

        home_score = detail["home"]["total_pts"]
        away_score = detail["away"]["total_pts"]
        games.append({
            "id": pid,
            "jornada": m["jornada"],
            "date": date_iso,
            "venue": m.get("venue"),
            "status": detail["status"],
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_score": home_score,
            "away_score": away_score,
            "winner": _winner(home_score, away_score, detail["status"]),
            "quarters": detail["quarters"],
            "has_box_score": True,
        })

        # ---- per-quarter aggregation from the play-by-play log ----
        # name -> period -> dict(pts, t2, t3, tl_made, tl_att, fp)
        per_player_q: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"pts": 0, "t2": 0, "t3": 0, "tl_made": 0, "tl_att": 0, "fp": 0})
        )

        for seq, e in enumerate(detail["log"]):
            kind, extra = classify_event(e.get("event", ""), e.get("event_kind", "other"))

            side = e["side"]
            team_id_ev = home_team_id if side == "home" else (away_team_id if side == "away" else None)
            pname = e.get("player_name")
            pdorsal = e.get("player_dorsal")
            player_ref = None
            if pname and team_id_ev:
                player_ref = get_player_id(team_id_ev, pname, pdorsal)

            period = e["period"]
            if pname and team_id_ev and period:
                bucket = per_player_q[(team_id_ev, _name_key(pname))][period]
                if kind == "made_2":
                    bucket["pts"] += 2
                    bucket["t2"] += 1
                elif kind == "made_3":
                    bucket["pts"] += 3
                    bucket["t3"] += 1
                elif kind == "ft_made":
                    bucket["pts"] += 1
                    bucket["tl_made"] += 1
                    bucket["tl_att"] += 1
                elif kind == "ft_missed":
                    bucket["tl_att"] += 1
                elif kind.startswith("foul_"):
                    bucket["fp"] += 1

            log_events.append({
                "game_id": pid,
                "seq": seq,
                "period": period,
                "clock": e.get("clock"),
                "side": side,
                "team_id": team_id_ev,
                "player_id": player_ref,
                "player_name": pname,
                "player_dorsal": pdorsal,
                "event": e.get("event"),
                "event_kind": kind,
                "ft_index": extra.get("ft_index") or e.get("ft_index"),
                "ft_of": extra.get("ft_of") or e.get("ft_of"),
                "ft_granted": extra.get("ft_granted"),
                "score_home": e.get("score_home"),
                "score_away": e.get("score_away"),
            })

        # ---- per-team box scores ----
        for side_label, side_team_id, team_box in [
            ("home", home_team_id, detail["home"]),
            ("away", away_team_id, detail["away"]),
        ]:
            for p in team_box["players"]:
                player_id = get_player_id(side_team_id, p["name"], p.get("dorsal"))
                played = is_played(p)
                by_quarter = {}
                if played:
                    by_quarter = {
                        period: dict(stats)
                        for period, stats in sorted(per_player_q[(side_team_id, _name_key(p["name"]))].items())
                    }
                player_game_stats.append({
                    "game_id": pid,
                    "player_id": player_id,
                    "team_id": side_team_id,
                    "side": side_label,
                    "dorsal": p.get("dorsal"),
                    "pts": p["pts"], "t2": p["t2"], "t3": p["t3"],
                    "tl_made": p["tl_made"], "tl_att": p["tl_att"], "fp": p["fp"],
                    "ft_pct": _round(p["tl_made"] / p["tl_att"], 3) if p["tl_att"] else None,
                    "played": played,
                    "by_quarter": by_quarter,
                })

    # ---- season aggregates per player (only games they actually played) ----
    player_season = _player_season_stats(player_game_stats)

    # ---- season aggregates per team ----
    team_season = _team_season_stats(games)

    # ---- final output ----
    return {
        "group": group_meta["group"],
        "teams": [
            {
                "id": tid,
                "name": name,
                "logo_filename": team_id_to_logo.get(tid),
            }
            for tid, name in sorted(teams_by_id.items(), key=lambda x: x[1])
        ],
        "players": [
            {
                "id": info["id"],
                "team_id": info["team_id"],
                "team_name": info["team_name"],
                "name": _best_display_name(info["name_variants"]),
                "name_variants": sorted(info["name_variants"]),
                "dorsals": sorted(info["dorsals"], key=lambda d: (len(d), d)),
            }
            for info in sorted(players.values(), key=lambda i: (i["team_name"] or "", i["name"]))
        ],
        "games": games,
        "player_game_stats": player_game_stats,
        "log_events": log_events,
        "player_season_stats": player_season,
        "team_season_stats": team_season,
    }


def _winner(home: int | None, away: int | None, status: str | None) -> str | None:
    if status != "FINALIZADO" or home is None or away is None:
        return None
    if home > away:
        return "home"
    if away > home:
        return "away"
    return "draw"


def _player_season_stats(pgs_rows: list[dict]) -> list[dict]:
    bucket: dict[str, dict] = {}
    for r in pgs_rows:
        if not r["played"]:
            continue
        key = r["player_id"]
        if key not in bucket:
            bucket[key] = {
                "player_id": r["player_id"],
                "team_id": r["team_id"],
                "games_played": 0,
                "totals": {"pts": 0, "t2": 0, "t3": 0, "tl_made": 0, "tl_att": 0, "fp": 0},
                "per_quarter_totals": defaultdict(
                    lambda: {"pts": 0, "t2": 0, "t3": 0, "tl_made": 0, "tl_att": 0, "fp": 0}
                ),
                "highs": {"pts": 0, "t3": 0, "t2": 0, "tl_made": 0, "fp": 0},
            }
        b = bucket[key]
        b["games_played"] += 1
        for k in b["totals"]:
            b["totals"][k] += r[k]
        for k in b["highs"]:
            if r[k] > b["highs"][k]:
                b["highs"][k] = r[k]
        for period, qs in r["by_quarter"].items():
            for k, v in qs.items():
                b["per_quarter_totals"][period][k] += v

    out: list[dict] = []
    for b in bucket.values():
        gp = b["games_played"]
        out.append({
            "player_id": b["player_id"],
            "team_id": b["team_id"],
            "games_played": gp,
            "totals": b["totals"],
            "averages": {k: _round(v / gp) for k, v in b["totals"].items()},
            "ft_pct": _round(b["totals"]["tl_made"] / b["totals"]["tl_att"], 3) if b["totals"]["tl_att"] else None,
            "highs": b["highs"],
            "per_quarter_totals": {p: dict(q) for p, q in sorted(b["per_quarter_totals"].items())},
            "per_quarter_averages": {
                p: {k: _round(v / gp) for k, v in q.items()}
                for p, q in sorted(b["per_quarter_totals"].items())
            },
        })
    out.sort(key=lambda r: (-r["totals"]["pts"], -r["games_played"]))
    return out


def _team_season_stats(games: list[dict]) -> list[dict]:
    bucket: dict[str, dict] = {}
    for g in games:
        if g["status"] != "FINALIZADO":
            continue
        if g["home_score"] is None or g["away_score"] is None:
            continue
        for side, tid, scored, against in [
            ("home", g["home_team_id"], g["home_score"], g["away_score"]),
            ("away", g["away_team_id"], g["away_score"], g["home_score"]),
        ]:
            if not tid:
                continue
            if tid not in bucket:
                bucket[tid] = {
                    "team_id": tid,
                    "games_played": 0, "wins": 0, "losses": 0, "draws": 0,
                    "points_for": 0, "points_against": 0,
                    "per_quarter_for": defaultdict(int),
                    "per_quarter_against": defaultdict(int),
                    "per_quarter_games": defaultdict(int),
                }
            b = bucket[tid]
            b["games_played"] += 1
            if scored > against:
                b["wins"] += 1
            elif scored < against:
                b["losses"] += 1
            else:
                b["draws"] += 1
            b["points_for"] += scored
            b["points_against"] += against
            for i, q in enumerate(g.get("quarters") or [], start=1):
                period = f"P{i}"
                qh, qa = q
                b["per_quarter_for"][period] += qh if side == "home" else qa
                b["per_quarter_against"][period] += qa if side == "home" else qh
                b["per_quarter_games"][period] += 1

    out: list[dict] = []
    for b in bucket.values():
        gp = b["games_played"]
        out.append({
            "team_id": b["team_id"],
            "games_played": gp,
            "wins": b["wins"], "losses": b["losses"], "draws": b["draws"],
            "win_pct": _round(b["wins"] / gp, 3) if gp else 0,
            "points_for": b["points_for"],
            "points_against": b["points_against"],
            "point_diff": b["points_for"] - b["points_against"],
            "avg_points_for": _round(b["points_for"] / gp) if gp else 0,
            "avg_points_against": _round(b["points_against"] / gp) if gp else 0,
            "per_quarter": {
                period: {
                    "points_for": b["per_quarter_for"][period],
                    "points_against": b["per_quarter_against"][period],
                    "avg_for": _round(b["per_quarter_for"][period] / b["per_quarter_games"][period]),
                    "avg_against": _round(b["per_quarter_against"][period] / b["per_quarter_games"][period]),
                }
                for period in sorted(b["per_quarter_for"])
            },
        })
    out.sort(key=lambda r: (-r["win_pct"], -r["point_diff"]))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("group_dir", type=Path, help="Path to data/<group-slug> directory")
    p.add_argument("--out", type=Path, help="Output JSON file (default: <group_dir>/database.json)")
    args = p.parse_args()

    out = args.out or (args.group_dir / "database.json")
    db = build_database(args.group_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Wrote {out}\n"
        f"  teams: {len(db['teams'])}\n"
        f"  players: {len(db['players'])}\n"
        f"  games: {len(db['games'])}\n"
        f"  player_game_stats: {len(db['player_game_stats'])}\n"
        f"  log_events: {len(db['log_events'])}\n"
        f"  player_season_stats: {len(db['player_season_stats'])}\n"
        f"  team_season_stats: {len(db['team_season_stats'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

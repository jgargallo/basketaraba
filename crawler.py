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
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://basketaraba.com/actadigital"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

log = logging.getLogger("basketaraba")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, sleep: float = 0.4):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.sleep = sleep

    def get(self, url: str) -> str:
        log.debug("GET %s", url)
        r = self.s.get(url, timeout=30)
        r.raise_for_status()
        time.sleep(self.sleep)
        return r.text


# ---------------------------------------------------------------------------
# Group / category resolution
# ---------------------------------------------------------------------------

@dataclass
class GroupRef:
    category_name: str    # e.g. "SENIOR MASCULINA 3ª"
    category_id: str      # e.g. "684037903b2cd"
    group_name: str       # e.g. "SENIOR MASCULINA 3ª-GRUPO A"
    group_id: str         # e.g. "68c8052bc76df"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().upper()
    return re.sub(r"\s+", " ", s)


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s


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


def _extract_group_id(html: str, group_name_norm: str) -> str | None:
    """In a dameJornada HTML, find the verCalendario id for the given group header."""
    soup = BeautifulSoup(html, "lxml")
    for header in soup.select("div.categoria h3"):
        if _norm(header.get_text()) != group_name_norm:
            continue
        # Look forward through following siblings until next categoria header.
        node = header.find_parent("div", class_="categoria")
        for sib in node.find_all_next():
            if sib.name == "div" and "categoria" in (sib.get("class") or []):
                break
            onclick = sib.get("onclick", "") if isinstance(sib, Tag) else ""
            m = re.search(r"verCalendario\('([a-f0-9]+)'\)", onclick)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Calendar parsing
# ---------------------------------------------------------------------------

@dataclass
class CalendarMatch:
    jornada: int
    jornada_date: str  # DD/MM/YYYY
    home_team: str
    away_team: str
    home_team_id: str
    away_team_id: str
    home_logo: str | None      # logo filename basename
    away_logo: str | None
    home_score: int | None
    away_score: int | None
    score: str | None          # raw "80 - 46" or None if not played


@dataclass
class SeasonCalendar:
    group_name: str
    teams: dict[str, str]  # id -> name
    matches: list[CalendarMatch]


def fetch_calendar(client: Client, group: GroupRef) -> tuple[str, SeasonCalendar]:
    html = client.get(f"{BASE}/calendario/{group.group_id}")
    return html, _parse_calendar(html, group.group_name)


def _parse_calendar(html: str, group_name: str) -> SeasonCalendar:
    soup = BeautifulSoup(html, "lxml")

    teams: dict[str, str] = {}
    sel = soup.select_one("#equipo")
    if sel:
        for opt in sel.select("option"):
            if opt.get("value") and opt["value"] != "-1":
                teams[opt["value"]] = opt.get_text(strip=True)

    matches: list[CalendarMatch] = []
    # Each jornada is a <table> with <thead><tr class="cabecera"> followed by
    # <tbody> with <tr class="partido ..."> rows.
    for table in soup.select("table"):
        head_row = table.select_one("tr.cabecera")
        if not head_row:
            continue
        cells = head_row.find_all("td")
        if len(cells) < 3:
            continue
        m_jor = re.search(r"JORNADA\s+(\d+)", cells[0].get_text())
        m_date = re.search(r"(\d{2}/\d{2}/\d{4})", cells[2].get_text())
        if not (m_jor and m_date):
            continue
        jornada = int(m_jor.group(1))
        jornada_date = m_date.group(1)

        for tr in table.select("tr.partido"):
            classes = tr.get("class") or []
            team_ids = [c.replace("Equipo", "") for c in classes if c.startswith("Equipo")]
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            # Strip the logo's text-equivalent away — names come from the td text.
            home = tds[0].get_text(strip=True)
            score_text = tds[1].get_text(strip=True)
            away = tds[2].get_text(strip=True)
            score = score_text if re.search(r"\d", score_text) else None
            m_score = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_text)
            home_score = int(m_score.group(1)) if m_score else None
            away_score = int(m_score.group(2)) if m_score else None
            home_img = tds[0].find("img")
            away_img = tds[2].find("img")
            home_logo = _logo_base(home_img["src"]) if home_img and home_img.get("src") else None
            away_logo = _logo_base(away_img["src"]) if away_img and away_img.get("src") else None
            matches.append(
                CalendarMatch(
                    jornada=jornada,
                    jornada_date=jornada_date,
                    home_team=home,
                    away_team=away,
                    home_team_id=team_ids[0] if len(team_ids) > 0 else "",
                    away_team_id=team_ids[1] if len(team_ids) > 1 else "",
                    home_logo=home_logo,
                    away_logo=away_logo,
                    home_score=home_score,
                    away_score=away_score,
                    score=score,
                )
            )

    return SeasonCalendar(group_name=group_name, teams=teams, matches=matches)


# ---------------------------------------------------------------------------
# Match-id resolution: walk weekly jornadas to map matchup → partido id
# ---------------------------------------------------------------------------

def _ddmmyyyy_to_monday(s: str) -> date:
    d = datetime.strptime(s, "%d/%m/%Y").date()
    return d - timedelta(days=d.weekday())


@dataclass
class JornadaMatchEntry:
    partido_id: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    status: str            # e.g. "FINALIZADO", "P3", "SIN EMPEZAR"
    venue: str | None
    starts_at: str | None  # "DD/MM/YYYY HH:MM"
    home_logo: str | None = None
    away_logo: str | None = None


def fetch_week_jornada(client: Client, category_id: str, monday: date) -> tuple[str, list[tuple[str, JornadaMatchEntry]]]:
    """Returns (raw_html, [(group_name, entry)]) for a Monday."""
    html = client.get(
        f"{BASE}/ajax/dameJornada.php?live=1&week={monday.isoformat()}&categoria={category_id}"
    )
    return html, _parse_week_jornada(html)


def _parse_week_jornada(html: str) -> list[tuple[str, JornadaMatchEntry]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, JornadaMatchEntry]] = []
    current_group: str | None = None
    # The flat HTML has <div class="categoria"> headers followed by match
    # containers (<div class="container2" onclick="verPartido(...)"> for
    # finished/in-progress matches; <div class="container"> for not-started).
    for node in soup.find("body").descendants if soup.find("body") else soup.descendants:
        if not isinstance(node, Tag):
            continue
        classes = node.get("class") or []
        if "categoria" in classes:
            h = node.find("h3")
            if h:
                current_group = _norm(h.get_text())
            continue
        if node.name != "div":
            continue
        if "container2" not in classes and "container" not in classes:
            continue
        # Top-level match block only; skip nested ones already visited.
        if node.find_parent("div", class_="container2") or node.find_parent("div", class_="container"):
            continue
        entry = _parse_match_block(node)
        if entry and current_group:
            out.append((current_group, entry))
    return out


def _parse_match_block(div: Tag) -> JornadaMatchEntry | None:
    onclick = div.get("onclick", "")
    m = re.search(r"verPartido\('([a-f0-9]+)'\)", onclick)
    partido_id = m.group(1) if m else ""

    status_td = div.select_one("table.top-info td")
    status = status_td.get_text(strip=True) if status_td else ""

    teams = div.select("table.scoreboard tr.team-score")
    if len(teams) < 2:
        return None

    def _name(tr: Tag) -> str:
        tds = tr.find_all("td")
        return tds[1].get_text(strip=True) if len(tds) >= 2 else ""

    def _score(tr: Tag) -> int | None:
        tds = tr.find_all("td")
        if len(tds) < 3:
            return None
        txt = tds[2].get_text(strip=True)
        return int(txt) if txt.isdigit() else None

    def _row_logo(tr: Tag) -> str | None:
        img = tr.select_one("img")
        return _logo_base(img.get("src")) if img and img.get("src") else None

    home, away = teams[0], teams[1]
    bottom_tds = div.select("table.bottom-info td")
    venue = bottom_tds[0].get_text(strip=True) if bottom_tds else None
    starts_at = bottom_tds[1].get_text(strip=True) if len(bottom_tds) > 1 else None

    if not partido_id:
        # Not-played-yet block — still useful for the index but no id to crawl.
        partido_id = ""

    return JornadaMatchEntry(
        partido_id=partido_id,
        home_team=_name(home),
        away_team=_name(away),
        home_score=_score(home),
        away_score=_score(away),
        status=status,
        venue=venue,
        starts_at=starts_at,
        home_logo=_row_logo(home),
        away_logo=_row_logo(away),
    )


# ---------------------------------------------------------------------------
# Match detail parsing
# ---------------------------------------------------------------------------

@dataclass
class PlayerStat:
    dorsal: str
    name: str
    pts: int
    t2: int   # made 2-pointers
    t3: int   # made 3-pointers
    tl_made: int
    tl_att: int
    fp: int   # personal fouls


@dataclass
class TeamBox:
    team: str
    color: str | None
    logo: str | None
    total_pts: int
    total_t2: int
    total_t3: int
    total_tl_made: int
    total_tl_att: int
    total_fp: int
    players: list[PlayerStat]


@dataclass
class LogEntry:
    period: str          # "P1".."P4" (or "PROL" etc.)
    clock: str           # "MM:SS" remaining in period
    side: str            # "home" / "away" / "neutral"
    event: str           # raw description, e.g. "3 Puntos", "Tiro libre 2/2 metido", "Tiempo Muerto"
    event_kind: str      # canonical: "made_2", "made_3", "ft_made", "ft_missed", "timeout", "period_end", "other"
    ft_made: int | None  # for free throws: 1 if made else 0
    ft_index: int | None  # X in "X/Y"
    ft_of: int | None     # Y in "X/Y"
    player_dorsal: str | None
    player_name: str | None
    score_home: int | None
    score_away: int | None


@dataclass
class MatchDetail:
    partido_id: str
    status: str
    starts_at: str | None
    category: str | None
    home: TeamBox
    away: TeamBox
    quarters: list[tuple[int, int]]   # [(home_q1, away_q1), ...]
    log: list[LogEntry]


def fetch_match(client: Client, partido_id: str) -> tuple[str, MatchDetail]:
    html = client.get(f"{BASE}/ajax/damePartido.php?partido={partido_id}")
    return html, _parse_match(html, partido_id)


_FT_RE = re.compile(r"Tiro libre\s+(\d+)\s*/\s*(\d+)\s+(metido|fallado)", re.IGNORECASE)


def _classify_event(desc: str) -> tuple[str, dict]:
    d = desc.strip()
    if d.lower().startswith("3 punto"):
        return "made_3", {}
    if d.lower().startswith("2 punto"):
        return "made_2", {}
    if d.lower().startswith("tiro libre"):
        m = _FT_RE.search(d)
        if m:
            x, y, status = int(m.group(1)), int(m.group(2)), m.group(3).lower()
            kind = "ft_made" if status == "metido" else "ft_missed"
            return kind, {"ft_index": x, "ft_of": y, "ft_made": 1 if status == "metido" else 0}
        return "ft_missed", {}
    if "tiempo muerto" in d.lower():
        return "timeout", {}
    if "fin de periodo" in d.lower():
        return "period_end", {}
    return "other", {}


def _parse_match(html: str, partido_id: str) -> MatchDetail:
    soup = BeautifulSoup(html, "lxml")

    # --- Header / scoreboard
    teams = soup.select("table.scoreboard tr.team-score")
    if len(teams) < 2:
        raise RuntimeError(f"Match {partido_id}: could not parse scoreboard")

    def _logo(tr: Tag) -> str | None:
        img = tr.select_one("img")
        return img.get("src") if img else None

    def _name(tr: Tag) -> str:
        tds = tr.find_all("td")
        return tds[1].get_text(strip=True) if len(tds) >= 2 else ""

    def _color(tr: Tag) -> str | None:
        tds = tr.find_all("td")
        if len(tds) < 2:
            return None
        style = tds[1].get("style", "")
        m = re.search(r"color:\s*(#[0-9A-Fa-f]{3,6})", style)
        return m.group(1).upper() if m else None

    def _score(tr: Tag) -> int:
        tds = tr.find_all("td")
        return int(tds[2].get_text(strip=True)) if len(tds) >= 3 and tds[2].get_text(strip=True).isdigit() else 0

    home_tr, away_tr = teams[0], teams[1]
    home_name, away_name = _name(home_tr), _name(away_tr)
    home_color, away_color = _color(home_tr), _color(away_tr)
    home_logo, away_logo = _logo(home_tr), _logo(away_tr)

    status = ""
    top = soup.select_one("table.top-info td")
    if top:
        status = top.get_text(strip=True)

    bottom_tds = soup.select("div.top-left table.bottom-info td")
    category = bottom_tds[0].get_text(strip=True) if bottom_tds else None
    starts_at = bottom_tds[1].get_text(strip=True) if len(bottom_tds) > 1 else None

    # --- Per-quarter scores: "| 7 - 17 | 17 - 16 | 10 - 19 | 15 - 11 |"
    quarters: list[tuple[int, int]] = []
    qdiv = soup.select_one("div.bottom-left > div")
    if qdiv:
        for chunk in re.findall(r"(\d+)\s*-\s*(\d+)", qdiv.get_text()):
            quarters.append((int(chunk[0]), int(chunk[1])))

    # --- Player tables (one per team, in display order home, away).
    tables = soup.select("div.team-tables table.actions-table")
    if len(tables) < 2:
        raise RuntimeError(f"Match {partido_id}: missing player tables")
    home_box = _parse_team_box(tables[0], home_name, home_color, home_logo)
    away_box = _parse_team_box(tables[1], away_name, away_color, away_logo)
    # Update box scores to match scoreboard (player table totals should match).
    home_box.total_pts = _score(home_tr) or home_box.total_pts
    away_box.total_pts = _score(away_tr) or away_box.total_pts

    # --- Play-by-play log.
    log_entries: list[LogEntry] = []
    home_logo_base = _logo_base(home_logo)
    away_logo_base = _logo_base(away_logo)
    for item in soup.select("div.elementoAccion"):
        period_el = item.select_one("span.ge-match-time-info")
        clock_el = item.select_one("span.ge-match-time-sec")
        if not period_el or not clock_el:
            continue
        period = period_el.get_text(strip=True)
        clock = clock_el.get_text(strip=True)

        side = "neutral"
        img = item.select_one(".pp-item-mes-info img")
        if img:
            base = _logo_base(img.get("src"))
            if base == home_logo_base:
                side = "home"
            elif base == away_logo_base:
                side = "away"

        desc_spans = item.select("span.pp-item-mes-info-text__desc")
        event = desc_spans[0].get_text(strip=True) if desc_spans else ""
        player_dorsal: str | None = None
        player_name: str | None = None
        if len(desc_spans) >= 2:
            m = re.match(r"#\s*(\S+)\s+(.+)", desc_spans[1].get_text(strip=True))
            if m:
                player_dorsal, player_name = m.group(1), m.group(2).strip()

        local_el = item.select_one("span.pp-item-mes-score__local")
        visitor_el = item.select_one("span.pp-item-mes-score__visitor")
        score_home = int(local_el.get_text(strip=True)) if local_el and local_el.get_text(strip=True).isdigit() else None
        score_away = int(visitor_el.get_text(strip=True)) if visitor_el and visitor_el.get_text(strip=True).isdigit() else None

        kind, extra = _classify_event(event)
        log_entries.append(LogEntry(
            period=period, clock=clock, side=side, event=event, event_kind=kind,
            ft_made=extra.get("ft_made"), ft_index=extra.get("ft_index"), ft_of=extra.get("ft_of"),
            player_dorsal=player_dorsal, player_name=player_name,
            score_home=score_home, score_away=score_away,
        ))

    return MatchDetail(
        partido_id=partido_id,
        status=status,
        starts_at=starts_at,
        category=category,
        home=home_box,
        away=away_box,
        quarters=quarters,
        log=log_entries,
    )


def _logo_base(src: str | None) -> str | None:
    if not src:
        return None
    return src.rsplit("/", 1)[-1]


def _parse_team_box(table: Tag, team: str, color: str | None, logo: str | None) -> TeamBox:
    # Header cells: PTS, T2, T3, TL, FP. The first cell is the player name.
    rows = table.select("tbody tr")
    players: list[PlayerStat] = []
    totals = (0, 0, 0, 0, 0, 0)  # pts, t2, t3, tl_made, tl_att, fp
    for tr in rows:
        tds = tr.find_all("td")
        if not tds:
            continue
        first = tds[0].get_text(strip=True)
        if first.lower().startswith("totales"):
            pts, t2, t3, tl_made, tl_att, fp = _read_stat_cells(tds[1:])
            totals = (pts, t2, t3, tl_made, tl_att, fp)
            continue
        m = re.match(r"#\s*(\S+)\s+(.+)", first)
        if not m:
            continue
        dorsal, name = m.group(1), m.group(2).strip()
        pts, t2, t3, tl_made, tl_att, fp = _read_stat_cells(tds[1:])
        players.append(PlayerStat(dorsal=dorsal, name=name, pts=pts, t2=t2, t3=t3,
                                  tl_made=tl_made, tl_att=tl_att, fp=fp))

    return TeamBox(
        team=team, color=color, logo=logo,
        total_pts=totals[0], total_t2=totals[1], total_t3=totals[2],
        total_tl_made=totals[3], total_tl_att=totals[4], total_fp=totals[5],
        players=players,
    )


def _read_stat_cells(cells: list[Tag]) -> tuple[int, int, int, int, int, int]:
    """Read [PTS, T2, T3, TL, FP] from cells. TL is 'made/att'."""
    def _int(t: str) -> int:
        t = t.strip()
        return int(t) if t.isdigit() else 0

    vals = [c.get_text(strip=True) for c in cells]
    # Some rows have padding/extra empty cells; filter blanks but keep order.
    vals = [v for v in vals if v != ""]
    # Expected layout: PTS T2 T3 TL FP
    pts = _int(vals[0]) if len(vals) > 0 else 0
    t2 = _int(vals[1]) if len(vals) > 1 else 0
    t3 = _int(vals[2]) if len(vals) > 2 else 0
    tl_made = tl_att = 0
    if len(vals) > 3 and "/" in vals[3]:
        m = re.match(r"(\d+)\s*/\s*(\d+)", vals[3])
        if m:
            tl_made, tl_att = int(m.group(1)), int(m.group(2))
    fp = _int(vals[4]) if len(vals) > 4 else 0
    return pts, t2, t3, tl_made, tl_att, fp


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _to_dict(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_dict(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_dict(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_raw(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def crawl(group_name: str, out_root: Path, sleep: float, force: bool) -> None:
    client = Client(sleep=sleep)

    log.info("Resolving group: %s", group_name)
    group = resolve_group(client, group_name)
    log.info("→ category_id=%s group_id=%s", group.category_id, group.group_id)

    out_dir = out_root / _slugify(group.group_name)
    raw_dir = out_dir / "raw"
    matches_dir = out_dir / "matches"

    # Calendar
    cal_html, calendar = fetch_calendar(client, group)
    _write_raw(raw_dir / "calendario.html", cal_html)
    log.info("Calendar: %d teams, %d matches across %d jornadas",
             len(calendar.teams), len(calendar.matches),
             len({m.jornada for m in calendar.matches}))

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

    # Walk weekly jornadas, collect partido ids that belong to our group
    jornada_entries: list[dict] = []  # flat index of every match
    seen_ids: set[str] = set()
    seen_match_keys: set[tuple[int, frozenset[str]]] = set()
    target_group = _norm(group.group_name)
    for jornada, monday in sorted(mondays_by_jornada.items()):
        raw_path = raw_dir / f"jornada_{monday.isoformat()}.html"
        if raw_path.exists() and not force:
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
            jornada_entries.append({
                "jornada": jornada,
                "monday": monday.isoformat(),
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "source": "jornada",
                **_to_dict(entry),
            })
            if entry.partido_id:
                seen_ids.add(entry.partido_id)
            if home_team_id and away_team_id:
                seen_match_keys.add((jornada, frozenset({home_team_id, away_team_id})))

    # Fold in any calendar matches that the weekly AJAX didn't surface
    # (walkovers / forfeit results have no acta digital and only appear here).
    added = 0
    for cm in calendar.matches:
        key = (cm.jornada, frozenset({cm.home_team_id, cm.away_team_id}))
        if key in seen_match_keys:
            continue
        if not (cm.home_team_id and cm.away_team_id):
            continue
        monday = mondays_by_jornada.get(cm.jornada)
        status = "FINALIZADO" if (cm.home_score is not None and cm.away_score is not None) else "SIN EMPEZAR"
        jornada_entries.append({
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
        seen_match_keys.add(key)
        added += 1
    if added:
        log.info("Added %d calendar-only matches (no acta digital)", added)

    jornada_entries.sort(key=lambda e: (e["jornada"], e.get("monday") or "", e.get("starts_at") or ""))

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
        if json_path.exists() and raw_path.exists() and not force:
            log.info("[%d/%d] %s (cached)", i, len(seen_ids), pid)
            continue
        log.info("[%d/%d] %s", i, len(seen_ids), pid)
        try:
            html, detail = fetch_match(client, pid)
        except Exception as exc:
            log.exception("Failed to fetch partido %s: %s", pid, exc)
            continue
        _write_raw(raw_path, html)
        _write_json(json_path, detail)

    log.info("Done. Output in %s", out_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("group", help="Group name, e.g. 'SENIOR MASCULINA 3ª-GRUPO A'")
    p.add_argument("--out", type=Path, default=Path("data"), help="Output root directory (default: ./data)")
    p.add_argument("--sleep", type=float, default=0.4, help="Seconds between HTTP requests (default: 0.4)")
    p.add_argument("--force", action="store_true", help="Re-download even if cached files exist")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    crawl(args.group, args.out, args.sleep, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

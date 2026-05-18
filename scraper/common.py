from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup, Tag

BASE = "https://basketaraba.com/actadigital"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def norm_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().upper()
    return re.sub(r"\s+", " ", value)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value


@dataclass
class GroupRef:
    category_name: str
    category_id: str
    group_name: str
    group_id: str


def extract_group_id(html: str, group_name_norm: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for header in soup.select("div.categoria h3"):
        if norm_text(header.get_text()) != group_name_norm:
            continue
        node = header.find_parent("div", class_="categoria")
        for sibling in node.find_all_next():
            if sibling.name == "div" and "categoria" in (sibling.get("class") or []):
                break
            onclick = sibling.get("onclick", "") if isinstance(sibling, Tag) else ""
            match = re.search(r"verCalendario\('([a-f0-9]+)'\)", onclick)
            if match:
                return match.group(1)
    return None


@dataclass
class CalendarMatch:
    jornada: int
    jornada_date: str
    home_team: str
    away_team: str
    home_team_id: str
    away_team_id: str
    home_logo: str | None
    away_logo: str | None
    home_score: int | None
    away_score: int | None
    score: str | None


@dataclass
class SeasonCalendar:
    group_name: str
    teams: dict[str, str]
    matches: list[CalendarMatch]


def parse_calendar(html: str, group_name: str) -> SeasonCalendar:
    soup = BeautifulSoup(html, "lxml")

    teams: dict[str, str] = {}
    select = soup.select_one("#equipo")
    if select:
        for option in select.select("option"):
            if option.get("value") and option["value"] != "-1":
                teams[option["value"]] = option.get_text(strip=True)

    matches: list[CalendarMatch] = []
    for table in soup.select("table"):
        head_row = table.select_one("tr.cabecera")
        if not head_row:
            continue
        cells = head_row.find_all("td")
        if len(cells) < 3:
            continue
        match_jornada = re.search(r"JORNADA\s+(\d+)", cells[0].get_text())
        match_date = re.search(r"(\d{2}/\d{2}/\d{4})", cells[2].get_text())
        if not (match_jornada and match_date):
            continue
        jornada = int(match_jornada.group(1))
        jornada_date = match_date.group(1)

        for row in table.select("tr.partido"):
            classes = row.get("class") or []
            team_ids = [class_name.replace("Equipo", "") for class_name in classes if class_name.startswith("Equipo")]
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            home = cells[0].get_text(strip=True)
            score_text = cells[1].get_text(strip=True)
            away = cells[2].get_text(strip=True)
            score = score_text if re.search(r"\d", score_text) else None
            match_score = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_text)
            home_score = int(match_score.group(1)) if match_score else None
            away_score = int(match_score.group(2)) if match_score else None
            home_image = cells[0].find("img")
            away_image = cells[2].find("img")
            home_logo = _logo_base(home_image["src"]) if home_image and home_image.get("src") else None
            away_logo = _logo_base(away_image["src"]) if away_image and away_image.get("src") else None
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


def ddmmyyyy_to_monday(value: str) -> date:
    parsed = datetime.strptime(value, "%d/%m/%Y").date()
    return parsed - timedelta(days=parsed.weekday())


@dataclass
class JornadaMatchEntry:
    partido_id: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    status: str
    venue: str | None
    starts_at: str | None
    home_logo: str | None = None
    away_logo: str | None = None


def parse_week_jornada(html: str) -> list[tuple[str, JornadaMatchEntry]]:
    soup = BeautifulSoup(html, "lxml")
    result: list[tuple[str, JornadaMatchEntry]] = []
    current_group: str | None = None
    nodes = soup.find("body").descendants if soup.find("body") else soup.descendants
    for node in nodes:
        if not isinstance(node, Tag):
            continue
        classes = node.get("class") or []
        if "categoria" in classes:
            heading = node.find("h3")
            if heading:
                current_group = norm_text(heading.get_text())
            continue
        if node.name != "div":
            continue
        if "container2" not in classes and "container" not in classes:
            continue
        if node.find_parent("div", class_="container2") or node.find_parent("div", class_="container"):
            continue
        entry = _parse_match_block(node)
        if entry and current_group:
            result.append((current_group, entry))
    return result


def _parse_match_block(block: Tag) -> JornadaMatchEntry | None:
    onclick = block.get("onclick", "")
    match = re.search(r"verPartido\('([a-f0-9]+)'\)", onclick)
    partido_id = match.group(1) if match else ""

    status_cell = block.select_one("table.top-info td")
    status = status_cell.get_text(strip=True) if status_cell else ""

    teams = block.select("table.scoreboard tr.team-score")
    if len(teams) < 2:
        return None

    def name_for(row: Tag) -> str:
        cells = row.find_all("td")
        return cells[1].get_text(strip=True) if len(cells) >= 2 else ""

    def score_for(row: Tag) -> int | None:
        cells = row.find_all("td")
        if len(cells) < 3:
            return None
        value = cells[2].get_text(strip=True)
        return int(value) if value.isdigit() else None

    def row_logo(row: Tag) -> str | None:
        image = row.select_one("img")
        return _logo_base(image.get("src")) if image and image.get("src") else None

    bottom_cells = block.select("table.bottom-info td")
    venue = bottom_cells[0].get_text(strip=True) if bottom_cells else None
    starts_at = bottom_cells[1].get_text(strip=True) if len(bottom_cells) > 1 else None

    return JornadaMatchEntry(
        partido_id=partido_id,
        home_team=name_for(teams[0]),
        away_team=name_for(teams[1]),
        home_score=score_for(teams[0]),
        away_score=score_for(teams[1]),
        status=status,
        venue=venue,
        starts_at=starts_at,
        home_logo=row_logo(teams[0]),
        away_logo=row_logo(teams[1]),
    )


@dataclass
class PlayerStat:
    dorsal: str
    name: str
    pts: int
    t2: int
    t3: int
    tl_made: int
    tl_att: int
    fp: int


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
    period: str
    clock: str
    side: str
    event: str
    event_kind: str
    ft_made: int | None
    ft_index: int | None
    ft_of: int | None
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
    quarters: list[tuple[int, int]]
    log: list[LogEntry]


_FT_RE = re.compile(r"Tiro libre\s+(\d+)\s*/\s*(\d+)\s+(metido|fallado)", re.IGNORECASE)


def parse_match(html: str, partido_id: str) -> MatchDetail:
    soup = BeautifulSoup(html, "lxml")

    teams = soup.select("table.scoreboard tr.team-score")
    if len(teams) < 2:
        raise RuntimeError(f"Match {partido_id}: could not parse scoreboard")

    def logo_for(row: Tag) -> str | None:
        image = row.select_one("img")
        return image.get("src") if image else None

    def name_for(row: Tag) -> str:
        cells = row.find_all("td")
        return cells[1].get_text(strip=True) if len(cells) >= 2 else ""

    def color_for(row: Tag) -> str | None:
        cells = row.find_all("td")
        if len(cells) < 2:
            return None
        style = cells[1].get("style", "")
        match = re.search(r"color:\s*(#[0-9A-Fa-f]{3,6})", style)
        return match.group(1).upper() if match else None

    def score_for(row: Tag) -> int:
        cells = row.find_all("td")
        if len(cells) < 3:
            return 0
        value = cells[2].get_text(strip=True)
        return int(value) if value.isdigit() else 0

    home_row, away_row = teams[0], teams[1]
    home_name, away_name = name_for(home_row), name_for(away_row)
    home_color, away_color = color_for(home_row), color_for(away_row)
    home_logo, away_logo = logo_for(home_row), logo_for(away_row)

    status = ""
    top = soup.select_one("table.top-info td")
    if top:
        status = top.get_text(strip=True)

    bottom_cells = soup.select("div.top-left table.bottom-info td")
    category = bottom_cells[0].get_text(strip=True) if bottom_cells else None
    starts_at = bottom_cells[1].get_text(strip=True) if len(bottom_cells) > 1 else None

    quarters: list[tuple[int, int]] = []
    quarter_div = soup.select_one("div.bottom-left > div")
    if quarter_div:
        for home_score, away_score in re.findall(r"(\d+)\s*-\s*(\d+)", quarter_div.get_text()):
            quarters.append((int(home_score), int(away_score)))

    tables = soup.select("div.team-tables table.actions-table")
    if len(tables) < 2:
        raise RuntimeError(f"Match {partido_id}: missing player tables")
    home_box = _parse_team_box(tables[0], home_name, home_color, home_logo)
    away_box = _parse_team_box(tables[1], away_name, away_color, away_logo)
    home_box.total_pts = score_for(home_row) or home_box.total_pts
    away_box.total_pts = score_for(away_row) or away_box.total_pts

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
        image = item.select_one(".pp-item-mes-info img")
        if image:
            base = _logo_base(image.get("src"))
            if base == home_logo_base:
                side = "home"
            elif base == away_logo_base:
                side = "away"

        desc_spans = item.select("span.pp-item-mes-info-text__desc")
        event = desc_spans[0].get_text(strip=True) if desc_spans else ""
        player_dorsal: str | None = None
        player_name: str | None = None
        if len(desc_spans) >= 2:
            match = re.match(r"#\s*(\S+)\s+(.+)", desc_spans[1].get_text(strip=True))
            if match:
                player_dorsal, player_name = match.group(1), match.group(2).strip()

        local_el = item.select_one("span.pp-item-mes-score__local")
        visitor_el = item.select_one("span.pp-item-mes-score__visitor")
        score_home = int(local_el.get_text(strip=True)) if local_el and local_el.get_text(strip=True).isdigit() else None
        score_away = int(visitor_el.get_text(strip=True)) if visitor_el and visitor_el.get_text(strip=True).isdigit() else None

        kind, extra = _classify_event(event)
        log_entries.append(
            LogEntry(
                period=period,
                clock=clock,
                side=side,
                event=event,
                event_kind=kind,
                ft_made=extra.get("ft_made"),
                ft_index=extra.get("ft_index"),
                ft_of=extra.get("ft_of"),
                player_dorsal=player_dorsal,
                player_name=player_name,
                score_home=score_home,
                score_away=score_away,
            )
        )

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


def _classify_event(description: str) -> tuple[str, dict]:
    # Normalise: collapse whitespace, strip, lowercase for all comparisons.
    text = re.sub(r"\s+", " ", description or "").strip().lower()
    if text.startswith("3 punto"):
        return "made_3", {}
    if text.startswith("2 punto"):
        return "made_2", {}
    if text.startswith("tiro libre"):
        match = _FT_RE.search(text)
        if match:
            ft_index, ft_of, status = int(match.group(1)), int(match.group(2)), match.group(3).lower()
            kind = "ft_made" if status == "metido" else "ft_missed"
            return kind, {"ft_index": ft_index, "ft_of": ft_of, "ft_made": 1 if status == "metido" else 0}
        return "ft_missed", {}
    if "tiempo muerto" in text:
        return "timeout", {}
    if "fin de periodo" in text:
        return "period_end", {}
    return "other", {}


def _logo_base(src: str | None) -> str | None:
    if not src:
        return None
    return src.rsplit("/", 1)[-1]


def _parse_team_box(table: Tag, team: str, color: str | None, logo: str | None) -> TeamBox:
    rows = table.select("tbody tr")
    players: list[PlayerStat] = []
    totals = (0, 0, 0, 0, 0, 0)
    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue
        first = cells[0].get_text(strip=True)
        if first.lower().startswith("totales"):
            pts, t2, t3, tl_made, tl_att, fp = _read_stat_cells(cells[1:])
            totals = (pts, t2, t3, tl_made, tl_att, fp)
            continue
        match = re.match(r"#\s*(\S+)\s+(.+)", first)
        if not match:
            continue
        dorsal, name = match.group(1), match.group(2).strip()
        pts, t2, t3, tl_made, tl_att, fp = _read_stat_cells(cells[1:])
        players.append(PlayerStat(dorsal=dorsal, name=name, pts=pts, t2=t2, t3=t3, tl_made=tl_made, tl_att=tl_att, fp=fp))

    return TeamBox(
        team=team,
        color=color,
        logo=logo,
        total_pts=totals[0],
        total_t2=totals[1],
        total_t3=totals[2],
        total_tl_made=totals[3],
        total_tl_att=totals[4],
        total_fp=totals[5],
        players=players,
    )


def _read_stat_cells(cells: list[Tag]) -> tuple[int, int, int, int, int, int]:
    def as_int(value: str) -> int:
        value = value.strip()
        return int(value) if value.isdigit() else 0

    values = [cell.get_text(strip=True) for cell in cells]
    values = [value for value in values if value != ""]
    pts = as_int(values[0]) if len(values) > 0 else 0
    t2 = as_int(values[1]) if len(values) > 1 else 0
    t3 = as_int(values[2]) if len(values) > 2 else 0
    tl_made = tl_att = 0
    if len(values) > 3 and "/" in values[3]:
        match = re.match(r"(\d+)\s*/\s*(\d+)", values[3])
        if match:
            tl_made, tl_att = int(match.group(1)), int(match.group(2))
    fp = as_int(values[4]) if len(values) > 4 else 0
    return pts, t2, t3, tl_made, tl_att, fp


def to_dict(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return {key: to_dict(getattr(obj, key)) for key in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(value) for value in obj]
    return obj


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_raw(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def detect_season(season_jornadas: dict) -> str:
    """Derive '<year>-<yy>' season label from the earliest jornada date.

    season_jornadas maps jornada numbers to ISO dates ("YYYY-MM-DD").
    Raises ValueError if the dict is empty.
    """
    if not season_jornadas:
        raise ValueError("season_jornadas is empty — cannot detect season")
    first_date = min(season_jornadas.values())  # "YYYY-MM-DD"
    year, month = int(first_date[:4]), int(first_date[5:7])
    if month >= 8:
        return f"{year}-{(year + 1) % 100:02d}"
    return f"{year - 1}-{year % 100:02d}"


__all__ = [
    "BASE",
    "USER_AGENT",
    "CalendarMatch",
    "GroupRef",
    "JornadaMatchEntry",
    "LogEntry",
    "MatchDetail",
    "PlayerStat",
    "SeasonCalendar",
    "TeamBox",
    "ddmmyyyy_to_monday",
    "detect_season",
    "extract_group_id",
    "norm_text",
    "parse_calendar",
    "parse_match",
    "parse_week_jornada",
    "slugify",
    "to_dict",
    "write_json",
    "write_raw",
]
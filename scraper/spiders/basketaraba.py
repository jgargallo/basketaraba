from __future__ import annotations

import os
import json
import time
from datetime import date, timedelta
from pathlib import Path

import scrapy
from bs4 import BeautifulSoup
from scrapy.http import TextResponse
from scrapy import signals

from scraper.common import (
    BASE,
    GroupRef,
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


def _write_metrics_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _emit_metrics(metrics: dict) -> None:
    metrics_out = os.environ.get("BASKETARABA_METRICS_OUT")
    if metrics_out:
        _write_metrics_file(Path(metrics_out), metrics)
    if os.environ.get("BASKETARABA_EMIT_METRICS_JSON") == "1":
        print(f"METRICS_JSON: {json.dumps(metrics, sort_keys=True)}", flush=True)


class BasketarabaSpider(scrapy.Spider):
    name = "basketaraba"
    allowed_domains = ["basketaraba.com"]

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    def __init__(self, group: str, out: str = "data", force: str | bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_at = time.monotonic()
        self.group_name = group
        self.out_root = Path(out)
        self.force = str(force).lower() in {"1", "true", "yes", "on"}
        self.target_group = _norm(group)
        self.group_ref: GroupRef | None = None
        self.out_dir: Path | None = None
        self.raw_dir: Path | None = None
        self.matches_dir: Path | None = None
        self.calendar = None
        self.mondays_by_jornada: dict[int, date] = {}
        self.name_to_team_id: dict[str, str] = {}
        self.logo_to_team_id: dict[str, str] = {}
        self.raw_entries: list[dict] = []
        self.seen_ids: set[str] = set()
        self.pending_weeks: set[date] = set()
        self.cached_calendar_reads = 0
        self.cached_week_reads = 0
        self.cached_match_reads = 0
        self.network_requests_scheduled = 0
        self.calendar_only_matches = 0
        self.index_finalized = False
        self.calendar_loaded_at: float | None = None
        self.index_finalized_at: float | None = None

    def start_requests(self):
        yield scrapy.Request(f"{BASE}/jornada", callback=self.parse_group_page)

    def parse_group_page(self, response: TextResponse):
        category_name = self._category_name()
        soup = BeautifulSoup(response.text, "lxml")
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

        monday = date.today() - timedelta(days=date.today().weekday())
        yield self._group_week_request(category_id, monday, 0)

    def parse_group_week(self, response: TextResponse, *, category_id: str, monday: date, offset: int):
        group_id = _extract_group_id(response.text, self.target_group)
        if group_id:
            self.group_ref = GroupRef(
                category_name=self._category_name(),
                category_id=category_id,
                group_name=self.target_group,
                group_id=group_id,
            )
            self.out_dir = self.out_root / _slugify(self.group_ref.group_name)
            self.raw_dir = self.out_dir / "raw"
            self.matches_dir = self.out_dir / "matches"
            yield from self._load_calendar()
            return

        if offset >= 29:
            raise RuntimeError(f"Could not find group {self.target_group!r} in any recent jornada")

        next_monday = monday - timedelta(weeks=1)
        yield self._group_week_request(category_id, next_monday, offset + 1)

    def parse_calendar_page(self, response: TextResponse):
        assert self.group_ref is not None
        assert self.raw_dir is not None

        _write_raw(self.raw_dir / "calendario.html", response.text)
        self.calendar = _parse_calendar(response.text, self.group_ref.group_name)
        self.logger.info(
            "Calendar: %d teams, %d matches across %d jornadas",
            len(self.calendar.teams),
            len(self.calendar.matches),
            len({m.jornada for m in self.calendar.matches}),
        )
        self.calendar_loaded_at = time.monotonic()

        self.mondays_by_jornada = {}
        for match in self.calendar.matches:
            self.mondays_by_jornada.setdefault(match.jornada, _ddmmyyyy_to_monday(match.jornada_date))

        self.name_to_team_id = {name: team_id for team_id, name in self.calendar.teams.items()}
        self.logo_to_team_id = {}
        for match in self.calendar.matches:
            if match.home_logo and match.home_team_id:
                self.logo_to_team_id.setdefault(match.home_logo, match.home_team_id)
            if match.away_logo and match.away_team_id:
                self.logo_to_team_id.setdefault(match.away_logo, match.away_team_id)

        self.pending_weeks = set(self.mondays_by_jornada.values())
        for jornada, monday in sorted(self.mondays_by_jornada.items()):
            yield from self._load_week(jornada, monday)

        if not self.pending_weeks and not self.index_finalized:
            yield from self._finalize_week_index()

    def parse_week_page(self, response: TextResponse, *, jornada: int, monday: date):
        assert self.raw_dir is not None

        _write_raw(self.raw_dir / f"jornada_{monday.isoformat()}.html", response.text)
        entries = _parse_week_jornada(response.text)
        in_group = [entry for group_name, entry in entries if group_name == self.target_group]
        self.logger.info("Jornada %d (%s): %d matches", jornada, monday.isoformat(), len(in_group))

        for entry in in_group:
            home_team_id = self._resolve_team_id(entry.home_team, entry.home_logo)
            away_team_id = self._resolve_team_id(entry.away_team, entry.away_logo)
            self.raw_entries.append(
                {
                    "jornada": jornada,
                    "monday": monday.isoformat(),
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "source": "jornada",
                    **_to_dict(entry),
                }
            )
            if entry.partido_id:
                self.seen_ids.add(entry.partido_id)

        self.pending_weeks.discard(monday)
        if not self.pending_weeks and not self.index_finalized:
            yield from self._finalize_week_index()

    def parse_match_page(self, response: TextResponse, *, partido_id: str):
        assert self.raw_dir is not None
        assert self.matches_dir is not None

        _write_raw(self.raw_dir / f"partido_{partido_id}.html", response.text)
        detail = _parse_match(response.text, partido_id)
        _write_json(self.matches_dir / f"{partido_id}.json", detail)

    def _category_name(self) -> str:
        if "-GRUPO" in self.target_group:
            return self.target_group.split("-GRUPO", 1)[0].strip()
        return self.target_group

    def _group_week_request(self, category_id: str, monday: date, offset: int) -> scrapy.Request:
        return scrapy.Request(
            f"{BASE}/ajax/dameJornada.php?live=1&week={monday.isoformat()}&categoria={category_id}",
            callback=self.parse_group_week,
            cb_kwargs={"category_id": category_id, "monday": monday, "offset": offset},
        )

    def _load_calendar(self):
        assert self.group_ref is not None
        assert self.raw_dir is not None

        raw_path = self.raw_dir / "calendario.html"
        url = f"{BASE}/calendario/{self.group_ref.group_id}"
        if raw_path.exists() and not self.force:
            self.cached_calendar_reads += 1
            response = self._cached_response(url, raw_path)
            yield from self.parse_calendar_page(response)
            return

        self.network_requests_scheduled += 1
        yield scrapy.Request(url, callback=self.parse_calendar_page)

    def _load_week(self, jornada: int, monday: date):
        assert self.group_ref is not None
        assert self.raw_dir is not None

        raw_path = self.raw_dir / f"jornada_{monday.isoformat()}.html"
        url = f"{BASE}/ajax/dameJornada.php?live=1&week={monday.isoformat()}&categoria={self.group_ref.category_id}"
        if raw_path.exists() and not self.force:
            self.cached_week_reads += 1
            response = self._cached_response(url, raw_path)
            yield from self.parse_week_page(response, jornada=jornada, monday=monday)
            return

        self.network_requests_scheduled += 1
        yield scrapy.Request(
            url,
            callback=self.parse_week_page,
            cb_kwargs={"jornada": jornada, "monday": monday},
            dont_filter=True,
        )

    def _finalize_week_index(self):
        assert self.group_ref is not None
        assert self.out_dir is not None
        assert self.raw_dir is not None
        assert self.matches_dir is not None
        assert self.calendar is not None

        self.index_finalized = True
        self.index_finalized_at = time.monotonic()

        for match in self.calendar.matches:
            monday = self.mondays_by_jornada.get(match.jornada)
            status = "FINALIZADO" if (match.home_score is not None and match.away_score is not None) else "SIN EMPEZAR"
            self.raw_entries.append(
                {
                    "jornada": match.jornada,
                    "monday": monday.isoformat() if monday else None,
                    "home_team_id": match.home_team_id,
                    "away_team_id": match.away_team_id,
                    "source": "calendar",
                    "partido_id": "",
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "home_score": match.home_score,
                    "away_score": match.away_score,
                    "status": status,
                    "venue": None,
                    "starts_at": None,
                    "home_logo": match.home_logo,
                    "away_logo": match.away_logo,
                }
            )

        bucket: dict[tuple[str, str], dict] = {}
        skipped = 0
        for entry in self.raw_entries:
            key = (entry.get("home_team_id") or "", entry.get("away_team_id") or "")
            if not (key[0] and key[1]):
                skipped += 1
                continue
            previous = bucket.get(key)
            if previous is None or self._entry_score(entry) > self._entry_score(previous):
                bucket[key] = entry

        if skipped:
            self.logger.warning("Skipped %d entries with unresolved team_ids", skipped)

        self.calendar_only_matches = sum(1 for entry in bucket.values() if entry.get("source") == "calendar")
        self.logger.info("Index: %d matches (%d from calendar-only, no acta)", len(bucket), self.calendar_only_matches)

        jornada_entries = sorted(
            bucket.values(),
            key=lambda entry: (entry["jornada"], entry.get("monday") or "", entry.get("starts_at") or ""),
        )
        _write_json(
            self.out_dir / "matches.json",
            {
                "group": _to_dict(self.group_ref),
                "matches": jornada_entries,
            },
        )
        _write_json(
            self.out_dir / "group.json",
            {
                "group": _to_dict(self.group_ref),
                "teams": self.calendar.teams,
                "season_jornadas": {
                    str(jornada): monday.isoformat()
                    for jornada, monday in sorted(self.mondays_by_jornada.items())
                },
            },
        )

        self.logger.info("Fetching %d matches with partido ids…", len(self.seen_ids))
        for index, partido_id in enumerate(sorted(self.seen_ids), 1):
            raw_path = self.raw_dir / f"partido_{partido_id}.html"
            json_path = self.matches_dir / f"{partido_id}.json"
            if json_path.exists() and raw_path.exists() and not self.force:
                self.cached_match_reads += 1
                self.logger.info("[%d/%d] %s (cached)", index, len(self.seen_ids), partido_id)
                continue

            self.logger.info("[%d/%d] %s", index, len(self.seen_ids), partido_id)
            url = f"{BASE}/ajax/damePartido.php?partido={partido_id}"
            if raw_path.exists() and not self.force:
                self.cached_match_reads += 1
                response = self._cached_response(url, raw_path)
                self.parse_match_page(response, partido_id=partido_id)
                continue

            self.network_requests_scheduled += 1
            yield scrapy.Request(url, callback=self.parse_match_page, cb_kwargs={"partido_id": partido_id})

    def _on_spider_closed(self, spider, reason: str) -> None:
        stats = self.crawler.stats
        finished_at = time.monotonic()
        calendar_loaded_at = self.calendar_loaded_at or finished_at
        index_finalized_at = self.index_finalized_at or finished_at
        metrics = {
            "engine": "scrapy",
            "reason": reason,
            "scheduled_network_requests": self.network_requests_scheduled,
            "cached_calendar": self.cached_calendar_reads,
            "cached_weeks": self.cached_week_reads,
            "cached_matches": self.cached_match_reads,
            "retries": stats.get_value("retry/count", 0),
            "calendar_only": self.calendar_only_matches,
            "http_200": stats.get_value("downloader/response_status_count/200", 0),
            "timings_s": {
                "resolve_and_calendar": round(calendar_loaded_at - self.started_at, 3),
                "index": round(index_finalized_at - calendar_loaded_at, 3),
                "detail": round(finished_at - index_finalized_at, 3),
                "total": round(finished_at - self.started_at, 3),
            },
        }
        self.logger.info(
            "Scrapy summary: reason=%s scheduled_network_requests=%d cached_calendar=%d cached_weeks=%d cached_matches=%d retries=%d calendar_only=%d http_200=%d timings_s(resolve_and_calendar=%.3f index=%.3f detail=%.3f total=%.3f)",
            metrics["reason"],
            metrics["scheduled_network_requests"],
            metrics["cached_calendar"],
            metrics["cached_weeks"],
            metrics["cached_matches"],
            metrics["retries"],
            metrics["calendar_only"],
            metrics["http_200"],
            metrics["timings_s"]["resolve_and_calendar"],
            metrics["timings_s"]["index"],
            metrics["timings_s"]["detail"],
            metrics["timings_s"]["total"],
        )
        _emit_metrics(metrics)

    def _resolve_team_id(self, name: str | None, logo: str | None) -> str:
        if name and name in self.name_to_team_id:
            return self.name_to_team_id[name]
        if logo and logo in self.logo_to_team_id:
            return self.logo_to_team_id[logo]
        return ""

    @staticmethod
    def _entry_score(entry: dict) -> tuple[bool, bool, bool, bool]:
        return (
            bool(entry.get("partido_id")),
            entry.get("source") == "jornada",
            entry.get("status") == "FINALIZADO",
            entry.get("home_score") is not None,
        )

    @staticmethod
    def _cached_response(url: str, path: Path) -> TextResponse:
        text = path.read_text(encoding="utf-8")
        return TextResponse(url=url, body=text.encode("utf-8"), encoding="utf-8")
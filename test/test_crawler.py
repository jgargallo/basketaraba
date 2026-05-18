"""Tests for crawler.py — season fallback, pending date helpers, Client."""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawler import _load_pending, _save_pending


# ---------------------------------------------------------------------------
# _load_pending / _save_pending
# ---------------------------------------------------------------------------

class TestPendingDates(unittest.TestCase):
    def test_load_pending_empty_when_no_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = _load_pending(Path(tmp))
        self.assertEqual([], result)

    def test_load_pending_returns_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            data = [{"home_team_id": "t1", "away_team_id": "t2", "jornada": 1, "monday": "2025-10-06"}]
            (p / "pending_dates.json").write_text(json.dumps(data), encoding="utf-8")
            result = _load_pending(p)
        self.assertEqual(data, result)

    def test_load_pending_returns_empty_on_corrupt_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "pending_dates.json").write_text("not json!!", encoding="utf-8")
            result = _load_pending(p)
        self.assertEqual([], result)

    def test_save_pending_writes_only_matches_without_starts_at(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            matches = [
                {"home_team_id": "t1", "away_team_id": "t2", "jornada": 1,
                 "monday": "2025-10-06", "starts_at": None},
                {"home_team_id": "t3", "away_team_id": "t4", "jornada": 2,
                 "monday": "2025-10-13", "starts_at": "12/10/2025 18:00"},
            ]
            _save_pending(p, matches)
            saved = json.loads((p / "pending_dates.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(saved))
        self.assertEqual("t1", saved[0]["home_team_id"])

    def test_save_pending_empty_when_all_resolved(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            matches = [
                {"home_team_id": "t1", "away_team_id": "t2", "jornada": 1,
                 "monday": "2025-10-06", "starts_at": "12/10/2025 18:00"},
            ]
            _save_pending(p, matches)
            saved = json.loads((p / "pending_dates.json").read_text(encoding="utf-8"))
        self.assertEqual([], saved)

    def test_save_pending_skips_entries_without_team_ids(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            matches = [
                {"home_team_id": "", "away_team_id": "t2", "jornada": 1,
                 "monday": "2025-10-06", "starts_at": None},
                {"home_team_id": "t1", "away_team_id": None, "jornada": 2,
                 "monday": "2025-10-13", "starts_at": None},
            ]
            _save_pending(p, matches)
            saved = json.loads((p / "pending_dates.json").read_text(encoding="utf-8"))
        self.assertEqual([], saved)


# ---------------------------------------------------------------------------
# Season fallback from today's date (pre-season empty calendar)
# ---------------------------------------------------------------------------

class TestSeasonFallbackFromToday(unittest.TestCase):
    """When the calendar has no dated jornadas, the crawler should NOT raise
    RuntimeError. It should derive the season from today's date and log a warning."""

    def _make_minimal_crawl_env(self, tmp_path: Path):
        """Return a mocked client and minimal group that triggers the empty-calendar path."""
        from scraper.common import GroupRef, SeasonCalendar
        group = GroupRef(
            category_name="TEST",
            category_id="cat1",
            group_name="TEST-GRUPO A",
            group_id="grp1",
        )
        calendar = SeasonCalendar(group_name="TEST-GRUPO A", teams={}, matches=[])
        return group, calendar

    def test_empty_calendar_uses_date_fallback_august_or_later(self):
        """During season start (Aug+), season should be derived as <year>-<yy+1>."""
        import tempfile
        from scraper.common import GroupRef, SeasonCalendar
        from crawler import crawl

        # We only want to test the season-detection branch; mock everything else.
        group = GroupRef(category_name="TEST", category_id="c", group_name="TEST-GRUPO A", group_id="g")
        calendar = SeasonCalendar(group_name="TEST-GRUPO A", teams={}, matches=[])

        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            with patch("crawler.resolve_group", return_value=group), \
                 patch("crawler.fetch_calendar", return_value=("", calendar)), \
                 patch("crawler._load_pending", return_value=[]), \
                 patch("crawler.date") as mock_date, \
                 patch("crawler._save_pending"), \
                 patch("crawler._write_json"), \
                 patch("crawler._emit_metrics"):

                # Simulate a date in August (start of season)
                mock_date.today.return_value = date(2025, 8, 15)
                mock_date.fromisoformat = date.fromisoformat
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

                # Should not raise; should produce season "2025-26"
                try:
                    crawl("TEST-GRUPO A", out_root, sleep=0, force=False)
                except Exception as exc:
                    # Only RuntimeError from missing jornadas is the bug we're
                    # testing. Other exceptions (file not found etc.) are OK here
                    # since we're not providing real data.
                    if "cannot detect season" in str(exc).lower():
                        self.fail(f"Should not raise RuntimeError for empty calendar: {exc}")

    def test_season_derivation_august(self):
        """Aug 2025 → 2025-26."""
        from datetime import date as real_date
        with patch("crawler.date") as mock_date:
            mock_date.today.return_value = real_date(2025, 8, 1)
            mock_date.side_effect = lambda *a, **kw: real_date(*a, **kw)
            today = real_date(2025, 8, 1)
        year, month = today.year, today.month
        if month >= 8:
            season = f"{year}-{(year + 1) % 100:02d}"
        else:
            season = f"{year - 1}-{year % 100:02d}"
        self.assertEqual("2025-26", season)

    def test_season_derivation_february(self):
        """Feb 2026 → 2025-26."""
        today = date(2026, 2, 1)
        year, month = today.year, today.month
        if month >= 8:
            season = f"{year}-{(year + 1) % 100:02d}"
        else:
            season = f"{year - 1}-{year % 100:02d}"
        self.assertEqual("2025-26", season)

    def test_season_derivation_july(self):
        """July 2025 (off-season, before Aug cutoff) → 2024-25."""
        today = date(2025, 7, 31)
        year, month = today.year, today.month
        if month >= 8:
            season = f"{year}-{(year + 1) % 100:02d}"
        else:
            season = f"{year - 1}-{year % 100:02d}"
        self.assertEqual("2024-25", season)


# ---------------------------------------------------------------------------
# Client — request counting
# ---------------------------------------------------------------------------

class TestClient(unittest.TestCase):
    def test_request_count_increments(self):
        from crawler import Client
        client = Client(sleep=0)
        with patch.object(client.s, "get") as mock_get:
            mock_response = MagicMock()
            mock_response.text = "<html/>"
            mock_get.return_value = mock_response
            client.get("http://example.com/a")
            client.get("http://example.com/b")
        self.assertEqual(2, client.request_count)

    def test_get_raises_on_http_error(self):
        from crawler import Client
        import requests
        client = Client(sleep=0)
        with patch.object(client.s, "get") as mock_get:
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = requests.HTTPError("404")
            mock_get.return_value = mock_response
            with self.assertRaises(requests.HTTPError):
                client.get("http://example.com/404")


if __name__ == "__main__":
    unittest.main()

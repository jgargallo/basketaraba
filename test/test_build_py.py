"""Tests for web/build.py — view-model builders, helpers, and network error handling."""
from __future__ import annotations

import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import URLError

# Make sure the repo root is importable when running from any CWD.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.build import (
    _season_from_path,
    _slugify,
    _safe_div,
    _materialize_local_logos,
    _download_logo,
    _build_index,
    build_league,
    build_game_views,
    build_team_views,
    build_player_views,
    _LOGO_NETWORK_ERRORS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_db(n_teams: int = 2, n_games: int = 0) -> dict:
    teams = [
        {"id": f"t{i}", "name": f"Team {i}", "logo_url": None, "logo_filename": None}
        for i in range(1, n_teams + 1)
    ]
    games = []
    for i in range(1, n_games + 1):
        games.append({
            "id": f"game{i}",
            "jornada": i,
            "date": f"2025-10-{i:02d}T18:00:00",
            "venue": None,
            "status": "FINALIZADO",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 70 + i,
            "away_score": 60,
            "winner": "home",
            "quarters": [[20, 15], [18, 14], [17, 16], [15 + i, 15]],
            "has_box_score": True,
        })
    team_season = [
        {
            "team_id": f"t{i}",
            "games_played": n_games,
            "wins": n_games if i == 1 else 0,
            "losses": 0 if i == 1 else n_games,
            "draws": 0,
            "win_pct": 1.0 if i == 1 else 0.0,
            "points_for": 70 * n_games,
            "points_against": 60 * n_games,
            "point_diff": 10 * n_games,
            "avg_points_for": 70.0,
            "avg_points_against": 60.0,
        }
        for i in range(1, n_teams + 1)
    ]
    return {
        "group": {"group_name": "Test Group", "group_id": "g1", "category_id": "c1"},
        "teams": teams,
        "players": [],
        "games": games,
        "player_game_stats": [],
        "log_events": [],
        "player_season_stats": [],
        "team_season_stats": team_season,
    }


# ---------------------------------------------------------------------------
# _season_from_path
# ---------------------------------------------------------------------------

class TestSeasonFromPath(unittest.TestCase):
    def test_season_scoped_layout(self):
        p = Path("/some/data/2025-26/senior-masculina/database.json")
        self.assertEqual("2025-26", _season_from_path(p))

    def test_legacy_flat_layout_returns_none(self):
        p = Path("/some/data/senior-masculina/database.json")
        self.assertIsNone(_season_from_path(p))

    def test_non_season_parent_returns_none(self):
        p = Path("/home/user/project/database.json")
        self.assertIsNone(_season_from_path(p))


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify(unittest.TestCase):
    def test_accents_stripped(self):
        self.assertEqual("senior-masculina-3a-grupo-a", _slugify("SENIOR MASCULINA 3ª-GRUPO A"))

    def test_spaces_become_dashes(self):
        self.assertEqual("hello-world", _slugify("Hello World"))

    def test_consecutive_dashes_collapsed(self):
        self.assertEqual("a-b-c", _slugify("a--b--c"))

    def test_leading_trailing_dashes_stripped(self):
        self.assertEqual("abc", _slugify("-abc-"))


# ---------------------------------------------------------------------------
# _safe_div
# ---------------------------------------------------------------------------

class TestSafeDiv(unittest.TestCase):
    def test_normal_division(self):
        self.assertAlmostEqual(0.5, _safe_div(1, 2))

    def test_zero_denominator_returns_none(self):
        self.assertIsNone(_safe_div(5, 0))


# ---------------------------------------------------------------------------
# _download_logo network error handling
# ---------------------------------------------------------------------------

class TestDownloadLogoNetworkErrors(unittest.TestCase):
    """_materialize_local_logos must not raise on any network failure."""

    def _run_materialize(self, side_effect):
        db = _minimal_db(n_teams=1)
        db["teams"][0]["logo_filename"] = "logo.png"
        db["teams"][0]["logo_url"] = "http://example.com/logo.png"

        with patch("web.build._download_logo", side_effect=side_effect):
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                _materialize_local_logos(db, out_dir)
        # After failure, logo_url should be None
        self.assertIsNone(db["teams"][0]["logo_url"])

    def test_url_error_does_not_raise(self):
        self._run_materialize(URLError("network error"))

    def test_socket_error_does_not_raise(self):
        self._run_materialize(socket.error("connection refused"))

    def test_socket_timeout_does_not_raise(self):
        self._run_materialize(socket.timeout("timed out"))

    def test_connection_error_does_not_raise(self):
        self._run_materialize(ConnectionError("connection error"))

    def test_os_error_does_not_raise(self):
        self._run_materialize(OSError("os error"))

    def test_logo_network_errors_tuple_covers_all(self):
        """_LOGO_NETWORK_ERRORS must include the five expected exception types."""
        for exc_type in (URLError, socket.error, socket.timeout, ConnectionError, OSError):
            self.assertTrue(
                issubclass(exc_type, _LOGO_NETWORK_ERRORS),
                f"{exc_type.__name__} not in _LOGO_NETWORK_ERRORS",
            )


# ---------------------------------------------------------------------------
# build_league
# ---------------------------------------------------------------------------

class TestBuildLeague(unittest.TestCase):
    def test_standings_count_matches_teams(self):
        db = _minimal_db(n_teams=3, n_games=2)
        result = build_league(db)
        self.assertEqual(3, len(result["standings"]))

    def test_standings_have_rank(self):
        db = _minimal_db(n_teams=2, n_games=1)
        result = build_league(db)
        ranks = [s["rank"] for s in result["standings"]]
        self.assertEqual([1, 2], ranks)

    def test_games_list_present_and_sorted(self):
        db = _minimal_db(n_teams=2, n_games=3)
        result = build_league(db)
        self.assertEqual(3, len(result["games"]))
        jornadas = [g["jornada"] for g in result["games"]]
        self.assertEqual(sorted(jornadas), jornadas)

    def test_totals_counts(self):
        db = _minimal_db(n_teams=2, n_games=2)
        result = build_league(db)
        self.assertEqual(2, result["totals"]["games"])
        self.assertEqual(2, result["totals"]["completed"])

    def test_empty_games_no_error(self):
        db = _minimal_db(n_teams=2, n_games=0)
        result = build_league(db)
        self.assertEqual(0, result["totals"]["games"])


# ---------------------------------------------------------------------------
# build_game_views
# ---------------------------------------------------------------------------

class TestBuildGameViews(unittest.TestCase):
    def test_games_without_id_skipped(self):
        db = _minimal_db(n_teams=2, n_games=0)
        db["games"].append({
            "id": None,
            "jornada": 1, "date": None, "venue": None,
            "status": None,
            "home_team_id": "t1", "away_team_id": "t2",
            "home_score": None, "away_score": None,
            "winner": None, "quarters": [], "has_box_score": False,
        })
        result = build_game_views(db)
        self.assertEqual(0, len(result))

    def test_game_view_contains_home_away(self):
        db = _minimal_db(n_teams=2, n_games=1)
        result = build_game_views(db)
        self.assertIn("game1", result)
        view = result["game1"]
        self.assertIn("home", view)
        self.assertIn("away", view)
        self.assertIn("log", view)


# ---------------------------------------------------------------------------
# _build_index
# ---------------------------------------------------------------------------

class TestBuildIndex(unittest.TestCase):
    def test_index_json_written_with_seasons(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "data").mkdir()
            groups_meta = [
                {"season": "2025-26", "id": "group-a", "name": "Group A",
                 "built_at": "2025-10-01T00:00:00Z", "stats": {}},
                {"season": "2024-25", "id": "group-b", "name": "Group B",
                 "built_at": "2025-10-01T00:00:00Z", "stats": {}},
            ]
            _build_index(groups_meta, out_dir)
            index_path = out_dir / "data" / "index.json"
            self.assertTrue(index_path.exists())
            index = json.loads(index_path.read_text())
            self.assertEqual("2025-26", index["current_season"])
            self.assertIn("2025-26", index["seasons"])
            self.assertIn("2024-25", index["seasons"])

    def test_index_unknown_season_for_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "data").mkdir()
            groups_meta = [
                {"season": None, "id": "group-a", "name": "Group A",
                 "built_at": "2025-10-01T00:00:00Z", "stats": {}},
            ]
            _build_index(groups_meta, out_dir)
            index = json.loads((out_dir / "data" / "index.json").read_text())
            self.assertIn("unknown", index["seasons"])


if __name__ == "__main__":
    unittest.main()

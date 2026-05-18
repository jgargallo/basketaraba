"""Tests for stats.py — classify_event, resolve_team_id, winner, season aggregates."""
from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats import classify_event, _winner, build_database, is_played


# ---------------------------------------------------------------------------
# classify_event
# ---------------------------------------------------------------------------

class TestClassifyEvent(unittest.TestCase):
    # Happy path
    def test_3_punto(self):
        kind, _ = classify_event("3 Punto metido", "other")
        self.assertEqual("made_3", kind)

    def test_2_punto(self):
        kind, _ = classify_event("2 Punto metido", "other")
        self.assertEqual("made_2", kind)

    def test_ft_made(self):
        kind, extra = classify_event("Tiro libre 1 / 2 metido", "other")
        self.assertEqual("ft_made", kind)
        self.assertEqual(1, extra["ft_index"])
        self.assertEqual(2, extra["ft_of"])

    def test_ft_missed(self):
        kind, _ = classify_event("Tiro libre 2 / 2 fallado", "other")
        self.assertEqual("ft_missed", kind)

    def test_timeout(self):
        kind, _ = classify_event("Tiempo muerto", "other")
        self.assertEqual("timeout", kind)

    def test_period_end(self):
        kind, _ = classify_event("Fin de periodo", "other")
        self.assertEqual("period_end", kind)

    # Foul types
    def test_foul_personal(self):
        kind, _ = classify_event("Falta personal", "other")
        self.assertEqual("foul_personal", kind)

    def test_foul_technical(self):
        kind, _ = classify_event("Falta técnica (2 TL)", "other")
        self.assertEqual("foul_technical", kind)

    def test_foul_technical_no_accent(self):
        kind, _ = classify_event("Falta tecnica", "other")
        self.assertEqual("foul_technical", kind)

    def test_foul_unsportsmanlike(self):
        kind, _ = classify_event("Antideportiva", "other")
        self.assertEqual("foul_unsportsmanlike", kind)

    def test_foul_disqualifying(self):
        kind, _ = classify_event("Descalificante", "other")
        self.assertEqual("foul_disqualifying", kind)

    def test_foul_starts_with_falta(self):
        kind, _ = classify_event("Falta", "other")
        self.assertEqual("foul_personal", kind)

    # Normalisation: double spaces must not break classification
    def test_double_space_3_punto(self):
        kind, _ = classify_event("3  Punto  metido", "other")
        self.assertEqual("made_3", kind)

    def test_double_space_falta_personal(self):
        kind, _ = classify_event("Falta  personal", "other")
        self.assertEqual("foul_personal", kind)

    def test_double_space_ft(self):
        kind, extra = classify_event("Tiro  libre  1 /  2  metido", "other")
        self.assertEqual("ft_made", kind)
        self.assertEqual(1, extra["ft_index"])

    def test_leading_whitespace(self):
        kind, _ = classify_event("  Timeout  ", "other")
        # "timeout" keyword not present, should fall through to "other"
        # (this is correct — it's not "tiempo muerto")
        self.assertEqual("other", kind)

    def test_empty_string_returns_fallback(self):
        kind, _ = classify_event("", "fallback_kind")
        self.assertEqual("fallback_kind", kind)

    def test_none_like_empty(self):
        # classify_event expects str but let's make sure "" edge is handled
        kind, _ = classify_event("", "other")
        self.assertEqual("other", kind)

    def test_ft_granted_parsed(self):
        _, extra = classify_event("Falta técnica (2 TL)", "other")
        self.assertEqual(2, extra.get("ft_granted"))

    # Fallback kind is passed through when no match
    def test_unknown_event_uses_fallback(self):
        kind, _ = classify_event("Salto entre dos", "custom_fallback")
        self.assertEqual("custom_fallback", kind)


# ---------------------------------------------------------------------------
# _winner
# ---------------------------------------------------------------------------

class TestWinner(unittest.TestCase):
    def test_home_wins(self):
        self.assertEqual("home", _winner(70, 60, "FINALIZADO"))

    def test_away_wins(self):
        self.assertEqual("away", _winner(60, 70, "FINALIZADO"))

    def test_draw(self):
        self.assertEqual("draw", _winner(65, 65, "FINALIZADO"))

    def test_not_finalizado(self):
        self.assertIsNone(_winner(70, 60, "SIN EMPEZAR"))

    def test_none_scores(self):
        self.assertIsNone(_winner(None, 60, "FINALIZADO"))
        self.assertIsNone(_winner(70, None, "FINALIZADO"))
        self.assertIsNone(_winner(None, None, "FINALIZADO"))

    def test_none_status(self):
        self.assertIsNone(_winner(70, 60, None))


# ---------------------------------------------------------------------------
# is_played
# ---------------------------------------------------------------------------

class TestIsPlayed(unittest.TestCase):
    def test_all_zeros_not_played(self):
        p = {"pts": 0, "t2": 0, "t3": 0, "tl_made": 0, "tl_att": 0, "fp": 0}
        self.assertFalse(is_played(p))

    def test_nonzero_pts_played(self):
        p = {"pts": 10, "t2": 0, "t3": 0, "tl_made": 0, "tl_att": 0, "fp": 0}
        self.assertTrue(is_played(p))

    def test_only_fp_played(self):
        p = {"pts": 0, "t2": 0, "t3": 0, "tl_made": 0, "tl_att": 0, "fp": 1}
        self.assertTrue(is_played(p))


# ---------------------------------------------------------------------------
# build_database — warning for missing match detail file
# ---------------------------------------------------------------------------

class TestBuildDatabaseWarnings(unittest.TestCase):
    def _make_group_dir(self, tmp_path: Path, partido_id: str = "abc123") -> Path:
        group_dir = tmp_path / "test-group"
        group_dir.mkdir()
        (group_dir / "matches").mkdir()
        (group_dir / "raw").mkdir()

        group_json = {
            "group": {"group_name": "Test Group", "group_id": "g1", "category_id": "c1"},
            "teams": {"t1": "Team One", "t2": "Team Two"},
            "season_jornadas": {"1": "2025-10-06"},
        }
        (group_dir / "group.json").write_text(json.dumps(group_json), encoding="utf-8")

        matches_json = {
            "group": {"group_name": "Test Group"},
            "matches": [
                {
                    "jornada": 1,
                    "monday": "2025-10-06",
                    "home_team_id": "t1",
                    "away_team_id": "t2",
                    "partido_id": partido_id,
                    "home_team": "Team One",
                    "away_team": "Team Two",
                    "home_score": None,
                    "away_score": None,
                    "status": "FINALIZADO",
                    "venue": None,
                    "starts_at": None,
                    "home_logo": None,
                    "away_logo": None,
                    "source": "jornada",
                }
            ],
        }
        (group_dir / "matches.json").write_text(json.dumps(matches_json), encoding="utf-8")
        return group_dir

    def test_missing_match_detail_emits_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            group_dir = self._make_group_dir(Path(tmp), partido_id="missing123")
            # Do NOT create matches/missing123.json
            with self.assertLogs("stats", level="WARNING") as cm:
                build_database(group_dir)
            self.assertTrue(
                any("missing123" in line for line in cm.output),
                f"Expected warning for missing123 in: {cm.output}",
            )

    def test_present_match_detail_no_missing_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            group_dir = self._make_group_dir(Path(tmp), partido_id="present123")
            # Create a minimal match detail file
            detail = {
                "partido_id": "present123",
                "status": "FINALIZADO",
                "starts_at": None,
                "category": "Test",
                "home": {
                    "team": "Team One", "color": None, "logo": None,
                    "total_pts": 70, "total_t2": 5, "total_t3": 3,
                    "total_tl_made": 4, "total_tl_att": 6, "total_fp": 10,
                    "players": [],
                },
                "away": {
                    "team": "Team Two", "color": None, "logo": None,
                    "total_pts": 60, "total_t2": 4, "total_t3": 2,
                    "total_tl_made": 3, "total_tl_att": 5, "total_fp": 8,
                    "players": [],
                },
                "quarters": [[20, 15], [18, 14], [17, 16], [15, 15]],
                "log": [],
            }
            (group_dir / "matches" / "present123.json").write_text(
                json.dumps(detail), encoding="utf-8"
            )
            import logging
            with self.assertLogs("stats", level="WARNING") as cm:
                # We need at least one log record; use a dummy warning to keep
                # assertLogs happy even when no warnings are emitted.
                logging.getLogger("stats").warning("__sentinel__")
                build_database(group_dir)
            # Make sure no "missing detail file" warning was emitted
            missing_warnings = [
                line for line in cm.output if "missing" in line.lower() and "present123" in line
            ]
            self.assertEqual([], missing_warnings)


# ---------------------------------------------------------------------------
# build_database — warning for unresolved team_id
# ---------------------------------------------------------------------------

class TestBuildDatabaseUnresolvedTeams(unittest.TestCase):
    def test_unresolved_team_emits_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            group_dir = Path(tmp) / "test-group"
            group_dir.mkdir()
            (group_dir / "matches").mkdir()
            (group_dir / "raw").mkdir()

            group_json = {
                "group": {"group_name": "Test Group", "group_id": "g1", "category_id": "c1"},
                "teams": {"t1": "Team One", "t2": "Team Two"},
                "season_jornadas": {"1": "2025-10-06"},
            }
            (group_dir / "group.json").write_text(json.dumps(group_json), encoding="utf-8")

            matches_json = {
                "group": {"group_name": "Test Group"},
                "matches": [
                    {
                        "jornada": 1,
                        "monday": "2025-10-06",
                        "home_team_id": "t1",
                        "away_team_id": "t2",
                        "partido_id": "xyz456",
                        "home_team": "Team One",
                        "away_team": "Team Two",
                        "home_score": None,
                        "away_score": None,
                        "status": "FINALIZADO",
                        "venue": None,
                        "starts_at": None,
                        "home_logo": None,
                        "away_logo": None,
                        "source": "jornada",
                    }
                ],
            }
            (group_dir / "matches.json").write_text(json.dumps(matches_json), encoding="utf-8")

            # Detail where team names do NOT appear in calendar — triggers warning
            detail = {
                "partido_id": "xyz456",
                "status": "FINALIZADO",
                "starts_at": None,
                "category": "Test",
                "home": {
                    "team": "UNKNOWN HOME TEAM", "color": None, "logo": None,
                    "total_pts": 70, "total_t2": 5, "total_t3": 3,
                    "total_tl_made": 4, "total_tl_att": 6, "total_fp": 10,
                    "players": [],
                },
                "away": {
                    "team": "UNKNOWN AWAY TEAM", "color": None, "logo": None,
                    "total_pts": 60, "total_t2": 4, "total_t3": 2,
                    "total_tl_made": 3, "total_tl_att": 5, "total_fp": 8,
                    "players": [],
                },
                "quarters": [],
                "log": [],
            }
            (group_dir / "matches" / "xyz456.json").write_text(
                json.dumps(detail), encoding="utf-8"
            )
            with self.assertLogs("stats", level="WARNING") as cm:
                build_database(group_dir)
            # At least one warning about unresolved team_id expected
            unresolved = [line for line in cm.output if "resolve" in line.lower() or "team_id" in line.lower()]
            self.assertTrue(len(unresolved) >= 1, f"No unresolved team warning in: {cm.output}")


if __name__ == "__main__":
    unittest.main()

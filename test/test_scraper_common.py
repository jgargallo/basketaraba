from __future__ import annotations

import json
import unittest
from pathlib import Path

from scraper.common import parse_calendar, parse_match, parse_week_jornada

ROOT = Path(__file__).resolve().parent.parent
GROUP_DIR = ROOT / "data" / "senior-masculina-3a-grupo-a"
RAW_DIR = GROUP_DIR / "raw"


class ScraperCommonRegressionTests(unittest.TestCase):
    def test_parse_calendar_matches_cached_season_shape(self):
        cal_path = RAW_DIR / 'calendario.html'
        if not cal_path.exists():
            self.skipTest("Calendar file not found")
        content = cal_path.read_text(encoding='utf-8')
        calendar = parse_calendar(content, 'SENIOR MASCULINA 3ª-GRUPO A')
        pairings = {(m.home_team, m.away_team) for m in calendar.matches}

        self.assertEqual(12, len(calendar.teams))
        self.assertEqual(132, len(calendar.matches))
        self.assertIn(("ATZURRA SUGOIAK", "VENTACLIM SUGARRAK LAUDIO"), pairings)
        self.assertIn(("AGURAIN SASKIBALOIA C", "C.B.MAKOAK"), pairings)

    def test_parse_week_jornada_keeps_group_matches(self):
        jor_path = RAW_DIR / 'jornada_2025-10-06.html'
        if not jor_path.exists():
            self.skipTest("Jornada file not found")
        content = jor_path.read_text(encoding='utf-8')
        entries = parse_week_jornada(content)
        target_group = "SENIOR MASCULINA 3A-GRUPO A"
        in_group = [m for g, m in entries if g == target_group]
        pairings = {(m.home_team, m.away_team) for m in in_group}
        empty_ids = [m for m in in_group if not m.partido_id]

        self.assertEqual(5, len(in_group))
        self.assertIn(("BAR MANAOS LAKUA", "CD EUREKA"), pairings)
        self.assertEqual(1, len(empty_ids))
        self.assertEqual("GAZALBIDE A", empty_ids[0].home_team)
        self.assertEqual("ANBOTO JATETXEA", empty_ids[0].away_team)

    def test_parse_match_matches_cached_json_contract(self):
        partido_id = '68c807c325015'
        html_path = RAW_DIR / f'partido_{partido_id}.html'
        json_path = GROUP_DIR / 'matches' / f'{partido_id}.json'
        if not html_path.exists() or not json_path.exists():
            self.skipTest("Cached match files not found")
        html = html_path.read_text(encoding='utf-8')
        expected = json.loads(json_path.read_text(encoding='utf-8'))

        detail = parse_match(html, partido_id)

        self.assertEqual(expected['status'], detail.status)
        self.assertEqual(expected['starts_at'], detail.starts_at)
        self.assertEqual(expected['category'], detail.category)
        self.assertEqual(expected['home']['team'], detail.home.team)
        self.assertEqual(expected['away']['team'], detail.away.team)
        self.assertEqual(expected['home']['total_pts'], detail.home.total_pts)
        self.assertEqual(expected['away']['total_pts'], detail.away.total_pts)
        self.assertEqual(expected['quarters'], [list(pair) for pair in detail.quarters])
        self.assertEqual(len(expected['log']), len(detail.log))
        self.assertEqual(expected['log'][0]['event_kind'], detail.log[0].event_kind)

    def test_matches_index_contains_calendar_only_entry(self):
        index_path = GROUP_DIR / 'matches.json'
        if not index_path.exists():
            self.skipTest("matches.json not found")
        matches_index = json.loads(index_path.read_text(encoding='utf-8'))
        calendar_only = [
            entry for entry in matches_index['matches']
            if entry['source'] == 'calendar' and entry['partido_id'] == ''
        ]

        self.assertEqual(4, len(calendar_only))
        self.assertIn(
            {
                'jornada': 1,
                'home_team': 'AGURAIN SASKIBALOIA C',
                'away_team': 'C.B.MAKOAK',
                'home_score': 50,
                'away_score': 57,
            },
            [
                {
                    'jornada': entry['jornada'],
                    'home_team': entry['home_team'],
                    'away_team': entry['away_team'],
                    'home_score': entry['home_score'],
                    'away_score': entry['away_score'],
                }
                for entry in calendar_only
            ],
        )


if __name__ == '__main__':
    unittest.main()
# basketaraba

Tooling to scrape, normalize, and visualize statistics for a group of the
[basketaraba.com](https://basketaraba.com/actadigital/jornada) basketball
league. Three independent stages produce a static website that can be rebuilt
from the raw data at any time.

```
crawler.py   ->  data/<group>/matches/*.json     (raw scraped data)
stats.py     ->  data/<group>/database.json      (normalized "DB" view)
web/build.py ->  web/dist/                       (static site)
```

Each stage reads only the output of the previous one, so you can re-run any of
them in isolation.

## Requirements

- Python 3.11+
- A virtual environment with `requests`, `beautifulsoup4`, `lxml` (see
  `requirements.txt`).

```
pip install -r requirements.txt
```

The repo was developed against the venv at
`/Users/jgargallo/.pyenv/versions/3.11.9/envs/basketaraba`.

## Quick start

```
source /path/to/venv/bin/activate

# 1. Scrape one group for the whole season
python crawler.py "SENIOR MASCULINA 3ª-GRUPO A"

# 2. Build the normalized database
python stats.py data/senior-masculina-3a-grupo-a

# 3. Generate the static website
python web/build.py data/senior-masculina-3a-grupo-a/database.json

# 4. Serve it locally
cd web/dist && python -m http.server 8000
# open http://localhost:8000
```

## 1. Crawler (`crawler.py`)

Downloads the season schedule, every match's player stats, and the full
play-by-play log for the group passed as argument.

```
python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" [--out data] [--sleep 0.4] [--force] [-v]
```

How it works:

1. Resolves the group name to a `category_id` by reading the dropdown on the
   jornada page, then walks recent weekly jornadas until it finds the
   `group_id`.
2. Fetches `calendario/<group_id>` to enumerate every jornada date and
   matchup, and to build a team roster.
3. For each jornada's Monday, fetches `ajax/dameJornada.php?week=...&categoria=...`
   and parses the section under the group's header to collect `partido` IDs.
4. For each partido, fetches `ajax/damePartido.php?partido=...` and parses
   scoreboard, per-quarter scores, both box scores, and the play-by-play log.

All HTML responses are cached on disk under `raw/`, so re-runs are nearly
instant. Pass `--force` to refresh.

### Output layout

```
data/<group-slug>/
  group.json                       group/category IDs, teams, jornada -> Monday map
  matches.json                     flat index of every match (status, score, partido_id)
  matches/<partido_id>.json        per-match: quarters, both team boxes, full log
  raw/
    calendario.html
    jornada_<YYYY-MM-DD>.html
    partido_<partido_id>.html
```

### Match JSON shape

Each `matches/<id>.json` contains:

- `partido_id`, `status`, `starts_at`, `category`
- `quarters`: `[[home_q1, away_q1], ...]` (supports overtime as `E1`, etc.)
- `home`, `away`: team box with `total_pts/t2/t3/tl_made/tl_att/fp` and a
  `players[]` list (each with `dorsal`, `name`, `pts`, `t2`, `t3`, `tl_made`,
  `tl_att`, `fp`)
- `log[]`: play-by-play entries with `period`, `clock`, `side`
  (`home/away/neutral`), `event` (raw description), `event_kind` (canonical:
  `made_2/made_3/ft_made/ft_missed/timeout/period_end/other`), `player_dorsal`,
  `player_name`, running `score_home`/`score_away`

## 2. Stats (`stats.py`)

Reads the crawler output and produces a single normalized `database.json`
shaped like relational tables, ready to load into a DB or feed the website.

```
python stats.py data/<group-slug> [--out path/to/database.json]
```

### Tables produced

| Table | What it holds |
|---|---|
| `group` | category + group IDs |
| `teams[]` | `{id, name, logo_filename}` |
| `players[]` | `{id, team_id, team_name, name, name_variants[], dorsals[]}` |
| `games[]` | `{id, jornada, date, venue, home/away_team_id, home/away_score, quarters, winner, status, has_box_score}` |
| `player_game_stats[]` | per-player per-game box score + `by_quarter` breakdown derived from the log |
| `log_events[]` | normalized play-by-play with foreign keys; foul events further classified as `foul_personal/technical/unsportsmanlike/disqualifying` with `ft_granted` parsed from `(N TL)` |
| `player_season_stats[]` | per (player, team): `games_played`, `totals`, `averages`, `ft_pct`, `highs`, `per_quarter_totals`, `per_quarter_averages` |
| `team_season_stats[]` | per team: `games_played`, `wins`, `losses`, `win_pct`, `points_for/against`, `point_diff`, `avg_points_for/against`, `per_quarter` |

### Key design choices

- **"Didn't play" rule.** A player is considered to have not played a game
  when every box-score stat is zero (`pts=t2=t3=tl_made=tl_att=fp=0`). The
  `player_game_stats` row is still emitted with `played: false` and an empty
  `by_quarter`, but it is excluded from `games_played`, totals, averages and
  highs so it doesn't pollute season aggregates.
- **Player identity is per team.** Keyed on `(team_id, normalized_name)` so a
  player who plays for two teams gets two rows, keeping all stats scoped to
  the team. Spelling variants like `CHUKWUBUIKE E.` / `Chukwubuike, E.` /
  `Chukwubuike,e` collapse to a single player; the cleanest spelling is
  picked automatically and all observed variants are kept under
  `name_variants`.
- **Team identity by logo, not name.** Calendar names are canonical, but
  match-detail pages sometimes use alternate sponsor names (e.g. `SUGARRAK
  LAUDIO` vs `VENTACLIM SUGARRAK LAUDIO`). The logo filename is used as a
  fallback key so both sides resolve to the same `team_id`.
- **Per-quarter breakdown is derived from the log** and cross-checked against
  box-score totals (PTS sums match for every played row on the verified data).
- **Overtime is supported.** Periods like `E1` flow through generically.

## 3. Web build (`web/build.py`)

Reads `database.json` and writes a fully static site under `web/dist/`.

```
python web/build.py data/<group-slug>/database.json [--out web/dist] [--src web/src]
```

The build script splits the database into purpose-built JSONs so each page
loads only what it needs:

| File | Approx size | Used by |
|---|---|---|
| `data/league.json` | ~70 KB | every page (standings, leaders, group meta, full schedule) |
| `data/teams/<id>.json` | ~25 KB | team page (roster with season stats, highlights, games, per-quarter) |
| `data/players/<id>.json` | ~12 KB | player page (game-by-game stats with per-quarter) |
| `data/games/<id>.json` | ~55 KB | game page (box scores + classified play-by-play) |

Then copies `index.html`, `styles.css`, `app.js` from `web/src/` to `web/dist/`.

### Frontend

Single-page application in `web/src/` with no build toolchain:

- Tailwind CSS via CDN (custom orange/ink palette, Inter font)
- Chart.js via CDN
- Hash router with routes: `#/league`, `#/teams`, `#/leaders`, `#/schedule`,
  `#/team/:id`, `#/player/:id`, `#/game/:id`
- Each route fetches its JSON on demand and caches it in memory

### Pages

- **League** (`#/league`, landing) - KPI tiles, full standings (rows link to
  team page), top scorers preview, last 10 results, all-teams grid.
- **Team** (`#/team/:id`, golden page) - hero with logo and W-L badges; KPI
  tiles (PF/p, PC/p, point diff, ranking); per-quarter bar chart (for vs
  against); season timeline line chart (with opponent tooltip); highlights
  cards (top scorer, top 3pt, top 2pt, best FT%, best single game, fewest
  fouls); sortable full roster (PJ, PTS/p, PTS, 2/p, 3/p, TL/p, TL%, F/p,
  single-game high); sortable games table.
- **Player** (`#/player/:id`) - hero with team link; KPI tiles (GP, PPG,
  high, FT%); bar chart of points per game (greyed = didn't play); radar
  chart of per-quarter averages; doughnut chart of scoring distribution
  (2 x t2, 3 x t3, ft_made); season totals table; sortable per-game log
  with per-quarter points.
- **Game** (`#/game/:id`) - scoreboard with logos and per-quarter grid; two
  sortable box scores; play-by-play timeline color-coded by team with
  period and event-kind filters (made 2, made 3, FT made/missed, fouls,
  timeouts), each entry showing the running score.
- **Leaders** (`#/leaders`) - six leaderboards: PPG, 3pp, 2pp, FT% (min 20
  attempts), total points, fewest fouls per game.
- **Schedule** (`#/schedule`) - full season grouped by jornada with mini
  result cards.

### Deployment

`web/dist/` is fully static; drop it on Netlify, GitHub Pages, S3, etc. The
basketaraba CDN serves team logos directly (referenced with
`referrerpolicy="no-referrer"`).

## Repository layout

```
basketaraba/
  crawler.py             stage 1: scrape
  stats.py               stage 2: normalize
  web/
    build.py             stage 3: build static site
    src/
      index.html         SPA shell
      styles.css         custom styles on top of Tailwind utilities
      app.js             router + page renderers + Chart.js setup
    dist/                generated; safe to delete and rebuild
  data/                  generated by crawler + stats; safe to delete and rebuild
  requirements.txt
```

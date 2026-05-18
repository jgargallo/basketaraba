# basketaraba

Stats scraper and static-site generator for the [basketaraba.com](https://basketaraba.com/actadigital/jornada) basketball league.

## Project Overview

Three independent pipeline stages, each reading only the previous stage's output:

```
crawler.py   →  data/<group>/                  (raw scraped JSON)
stats.py     →  data/<group>/database.json     (normalized tables)
web/build.py →  web/dist/                      (static site)
```

## Project Structure

```
basketaraba/
  crawler.py             stage 1: scrape basketaraba.com
  stats.py               stage 2: normalize into relational tables
  web/
    build.py             stage 3: build static site from database.json
    src/
      index.html         SPA shell (hash router, no build toolchain)
      styles.css         custom styles on top of Tailwind CDN
      app.js             router + page renderers + Chart.js (~982 lines)
    dist/                generated; safe to delete and rebuild (gitignored)
  data/
    senior-masculina-3a-grupo-a/
      group.json         committed
      matches.json       committed
      matches/*.json     committed — canonical per-match data; CI skips already-scraped
      database.json      committed — derived tables
      raw/               gitignored — local download cache only, ephemeral in CI
  .github/
    workflows/
      scrape.yml         daily scrape + commit (06:00 UTC)
      pages.yml          build + GitHub Pages deploy (triggers on push to main)
  requirements.txt
  .codegraph/            CodeGraph semantic index
```

## Languages & Environment

- **Python 3.11+** — pipeline scripts (`crawler.py`, `stats.py`, `web/build.py`)
- **Vanilla JS + Tailwind CDN + Chart.js CDN** — frontend (no build step, no npm)
- Dependencies: `requests`, `beautifulsoup4`, `lxml` (see `requirements.txt`)

## Running the Pipeline

```bash
source /path/to/venv/bin/activate

# Stage 1 — scrape
python crawler.py "SENIOR MASCULINA 3ª-GRUPO A"
# options: --out data  --sleep 0.4  --force  -v

# Stage 2 — normalize
python stats.py data/senior-masculina-3a-grupo-a

# Stage 3 — build static site
python web/build.py data/senior-masculina-3a-grupo-a/database.json

# Serve locally
cd web/dist && python -m http.server 8000
```

### Run all groups at once

```bash
# Process all groups active in the current and previous week (default, fast)
python scripts/run_all_groups.py --no-serve --skip-build

# Process all groups found across the full season (slower, ~30× more HTTP requests)
python scripts/run_all_groups.py --full-season --no-serve --skip-build

# Discover groups only (prints JSON list)
python crawler.py --list-groups              # current + previous week
python crawler.py --list-groups --full-season  # full 30-week scan
```

`run_all_groups.py` options:

| Flag | Effect |
|------|--------|
| `--full-season` | Scan 30 past weeks when discovering groups (default: 2) |
| `--force` | Pass `--force` to crawler (refresh HTML cache) |
| `--skip-crawl` | Skip crawler, load groups from existing `data/*/database.json` |
| `--skip-stats` | Skip `stats.py` step |
| `--skip-build` | Skip `web/build.py` step |
| `--no-serve` | Don't start local HTTP server after build |

### Resolve pending dates only

Fixes matches without a `starts_at` date by scanning jornada pages (±4 weeks
around each match's scheduled monday). Much faster than a full crawl — no
calendar re-fetch, no match-detail downloads.

```bash
# All groups with pending_dates.json
python scripts/resolve_pending.py

# One specific group
python scripts/resolve_pending.py data/senior-masculina-3a-grupo-a

# Faster (smaller sleep between requests)
python scripts/resolve_pending.py --sleep 0.2

# Verbose (shows each HTTP request and resolved match)
python scripts/resolve_pending.py -v
```

Updates `matches.json` (adds `starts_at`) and shrinks `pending_dates.json`
for each resolved match. Run `stats.py` + `web/build.py --all` afterwards to
propagate dates to the site.

## Key Architecture Decisions

- **Disk cache** — all HTML responses are saved under `data/<group>/raw/` so re-runs skip network; pass `--force` to refresh.
- **Player identity** — keyed on `(team_id, normalized_name)`. A player on two teams gets two rows. Spelling variants collapse via `_name_key()` (strips accents, punctuation, casing).
- **"Didn't play" rule** — a player-game row with all-zero stats gets `played: false` and is excluded from aggregates, but the row is still emitted.
- **Team identity fallback** — logo filename is the secondary key when calendar and match-detail pages use different sponsor names.
- **Per-quarter breakdown** — derived from the play-by-play log, then cross-checked against box-score totals.
- **Overtime support** — period labels like `E1` flow through generically.
- **Group discovery filter** — `discover_groups()` skips any group that lacks a `verCalendario()` button (elimination rounds: F4, CRUCES, FINAL COPA). These events have no season calendar and cannot be scraped through the normal flow.
- **Heading vs canonical name** — the jornada HTML uses abbreviated h3 headings (e.g. `SEN.MAS.1A-GRUPO UNICO`). `discover_groups()` passes both the raw heading (for h3 matching) and the canonical name (built from the dropdown option text, used as the output directory slug) through the pipeline via `--heading` and `--category-id` flags.
- **Logo download resilience** — `web/build.py` catches `URLError` when downloading team logos and sets `logo_url = None` rather than crashing the build.
- **Pending-date resolution** — after each crawl, matches with `starts_at: null` are written to `data/<group>/pending_dates.json` (fields: `home_team_id`, `away_team_id`, `jornada`, `monday`). At the start of the next crawl, each pending match's scheduled `monday` is used as the anchor: jornada pages for ±4 weeks around that date are fetched fresh (bypassing cache) and searched for a match between those two teams. Resolved matches are promoted to `source: "jornada"` and win the dedup step over the calendar-only entry.
- **Live-match guard** — the crawler discards the detail JSON for any match whose `status` is not `FINALIZADO` or `SUSPENDIDO` at fetch time (e.g. `P3` for "playing in period 3"). This prevents in-progress scores from being committed. The match will appear as calendar-only on the next successful scrape once the acta is published.
- **Calendar-only games** — matches with no `partido_id` (score in the calendar but no acta page) produce a `games` row with `id: null` and `has_box_score: false`. Their date is parsed from the calendar's `starts_at` field (supports `dd/mm/yyyy` and `dd/mm/yyyy HH:MM`) and stored as ISO 8601 in `games[].date`. The frontend renders these cards muted and non-clickable.
- **Bypassing group discovery for finished seasons** — `crawler.py --category-id <id> --group-id <id>` skips the `dameJornada` scan entirely. The IDs are stored in each group's `group.json` and are needed when a season is over and the group no longer appears in recent weekly jornada pages.

## Data Shapes

### `matches/<id>.json` (crawler output)
- `partido_id`, `status`, `starts_at`, `category`
- `quarters`: `[[home_q1, away_q1], ...]`
- `home/away`: box score with `total_pts`, `t2`, `t3`, `tl_made`, `tl_att`, `fp`, `players[]`
- `log[]`: play-by-play with `period`, `clock`, `side`, `event`, `event_kind`, `player_dorsal`, `player_name`, `score_home`, `score_away`

### `database.json` tables (stats output)
`group`, `teams[]`, `players[]`, `games[]`, `player_game_stats[]`, `log_events[]`, `player_season_stats[]`, `team_season_stats[]`

Key `games[]` fields:
- `id` — `partido_id` (null for calendar-only games without an acta)
- `date` — ISO 8601 datetime (e.g. `2025-10-12T17:00:00`), derived from `matches.json` `starts_at`; null when `starts_at` was not yet known at crawl time
- `has_box_score` — false for calendar-only games (no acta scraped)
- `status` — `FINALIZADO`, `SUSPENDIDO`, or null for future games

> **Field naming**: `starts_at` is the raw string in `matches.json` (format `dd/mm/yyyy HH:MM`). `stats.py` converts it to ISO 8601 and stores it as `date` in `database.json` / `league.json`. Always use `game.date` in frontend code.

## Frontend Routes (SPA)

| Hash | Page |
|------|------|
| `#/league` | Landing: standings, top scorers, results |
| `#/teams` | Teams grid |
| `#/leaders` | Six leaderboards (PPG, 3pp, 2pp, FT%, total pts, fewest fouls) |
| `#/schedule` | Full season by jornada |
| `#/team/:id` | Team detail: charts, roster, games |
| `#/player/:id` | Player detail: charts, per-game log |
| `#/game/:id` | Box scores + filtered play-by-play |

`web/dist/` is fully static — deploy to Netlify, GitHub Pages, S3, etc. Team logos are served from `basketaraba.com` CDN with `referrerpolicy="no-referrer"`.

## CodeGraph

CodeGraph is initialized (`.codegraph/`). Use it for code exploration:

```bash
# In subagents, prefer codegraph_explore over direct file reads
# In main session, use lightweight tools:
#   codegraph_search, codegraph_callers, codegraph_callees, codegraph_impact, codegraph_node
```

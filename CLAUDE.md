# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Scrapes Spotify Charts (weekly regional CSVs) and the Spotify Web API to analyze top Japan / South Korea / Global tracks over ~3 months (2025-10 ~ 2025-12). Originally a ccClub Python Fall 2025 project written in Google Colab; it has since been moved to GitHub and is being run locally. All code lives in Jupyter notebooks — there is no Python package, build step, or test suite. Code comments and notes are in Traditional Chinese.

## Environment & tooling

- Always invoke `./.venv/bin/python` (e.g. `./.venv/bin/python -m pip install -r requirements.txt`) — never the system python.
- Notebooks are executed manually by a human in Jupyter with the kernel "Python 3.12.8 (venv-bigquery-pipeline)".
  - `requirements.txt` does not currently list `selenium`, `matplotlib`, or `seaborn`, which the notebooks import.
- Notebook outputs are stripped on commit via `nbstripout` (`.gitattributes` sets `filter=nbstripout` for `*.ipynb`; the filter is configured in local git config against `.venv/bin/python -m nbstripout`). Don't commit notebook outputs.
- pre-commit (`.pre-commit-config.yaml`): trailing-whitespace, end-of-file-fixer, check-yaml, detect-private-key, `no-commit-to-branch` (blocks commits to `main` — work on a feature branch), check-added-large-files (1000 KB), `ruff --fix`, `ruff-format`.
  - Run all hooks: `pre-commit run --all-files`
  - Lint/format directly: `ruff check --fix .` / `ruff format .`
- `.gitignore` excludes raw data and secrets: `data/`, `downloads/`, `output/`, `selenium-profile/`, `*.csv`, `*.json`, `.env`. Credentials (`CLIENT_ID`, `CLIENT_SECRET`) come from `.env` via python-dotenv or `input()`.

## Hard constraints

- The scraping step requires a human to log in manually in the Chrome window Selenium opens; this cannot be done for them. Never try to run cells that use Selenium, and never switch the driver to headless.
- To verify logic, write standalone tests or use fake data — do not run the real scraper.
- BigQuery uploads write only to the `spotify-chart` dataset. Do not touch any other (production) dataset.

## Pipeline architecture

Notebooks run in order N01 → N05; later notebooks depend on earlier ones via `%run` (not imports), so the notebook-global variable names are the de facto interface.

1. **N01_spotifychart_manlogin_download** (Extract) — Selenium opens charts.spotify.com, waits for the user to **log in manually** (`input()`), then downloads weekly CSVs for `jp`, `kr`, `global`. `get_uri()` derives chart URLs from each month's Thursdays (charts cover Fri–Thu; URL is `regional-{region}-weekly/{YYYY-MM-DD}` of the Thursday). The download button is located by a brittle hashed CSS selector. Chromedriver path and download dir are placeholders to fill in.
2. **N02_csv_extract_trackID** — `collect_track_ids_by_region()` reads all raw chart CSVs whose filename contains `regional-{region}-weekly`, splits the `uri` column (`spotify:track:<id>`) and dedupes into sets. Exposes globals `jp_track_id_set_in_3m`, `kr_track_id_set_in_3m`, `global_track_id_set_in_3m`.
3. **N03_API_general_extract** — Client-credentials auth: `get_token()`, `get_auther_header()`, `get_spotify_data_track(url, token, sleeptime)` (sleeps to avoid 429s; returns `None` on non-200).
4. **N04_API_extract_by_trackID** (Extract) — `%run`s N02 and N03, calls `GET /v1/tracks/{id}` per track ID, and writes `{jp,kr,global}_API_data_YYYYMMDD.csv` (`utf-8-sig`, refuses to overwrite existing files). The three region blocks are copy-pasted.
5. **N05_Data_Visualization** (Transform/Analyze) — Reads the latest 2026 weekly chart CSVs, derives `uri_cleaned` and `region`, and plots source share and streams share (Top10 / 11–20 / 20–200) with matplotlib; requires Noto CJK fonts for JP/KR titles.

Data directories are referenced as `<google_drive_path_to>/02_Data/01_Spotify_chart_raw` (N01 output) and `.../02_Spotify_API_raw` (N04 output). Colab-specific cells (`google.colab.drive.mount`, `!apt-get`, `!pip`) remain and must be replaced/skipped when running locally.

## Known issues in the current code

- N04 hardcodes `"country_chart": "JP"` in the KR and Global blocks, and uses `pandas.DataFrame` without importing `pandas` (N02 imports it as `pd`).
- N04 calls `get_token()` for every track request.
- N02 has a path marked as wrong in a comment ("路徑有錯").

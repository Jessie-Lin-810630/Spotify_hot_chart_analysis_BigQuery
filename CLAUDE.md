# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Scrapes Spotify Charts (weekly regional CSVs) and the Spotify Web API to analyze top Japan / South Korea / Global tracks (chart data currently covers 2025-10 ~ 2026-09). Originally a ccClub Python Fall 2025 project written in Google Colab; it has since been moved to GitHub and is being run locally. The chart ETL (extract → clean → load to BigQuery) has been migrated from notebooks to plain scripts in `src/`; the Spotify Web API and visualization steps are still Jupyter notebooks (N02–N05). There is no Python package, build step, or test suite. Code comments and notes are in Traditional Chinese.

## Environment & tooling

- Always invoke `./.venv/bin/python` (e.g. `./.venv/bin/python -m pip install -r requirements.txt`) — never the system python.
- Run scripts from the project root: `./.venv/bin/python src/<script>.py`. Parameters are hardcoded in each script's `if __name__ == "__main__":` block (the user prefers this over `argparse`).
- Scripts log via `logging` (`logger = logging.getLogger(__name__)`, `logging.basicConfig` only inside `__main__`) — use `logger.info/warning/error/exception`, not `print`.
- Notebooks (N02–N05) are executed manually by a human in Jupyter with the kernel "Python 3.12.8 (venv-bigquery-pipeline)".
  - `requirements.txt` does not list `matplotlib` or `seaborn`, which N05 imports (only `matplotlib-inline` is present). Don't install them — visualization is planned to move to Data Studio.
- Notebook outputs are stripped on commit via `nbstripout` (`.gitattributes` sets `filter=nbstripout` for `*.ipynb`; the filter is configured in local git config against `.venv/bin/python -m nbstripout`). Don't commit notebook outputs.
- pre-commit (`.pre-commit-config.yaml`): trailing-whitespace, end-of-file-fixer, check-yaml, detect-private-key, `no-commit-to-branch` (blocks commits to `main` — work on a feature branch), check-added-large-files (1000 KB), `ruff --fix`, `ruff-format`.
  - Run all hooks: `pre-commit run --all-files`
  - Lint/format directly: `./.venv/bin/python -m ruff check --fix src` / `./.venv/bin/python -m ruff format src`
  - `ruff --fix` rewriting a file fails the hook; re-`git add` and commit again.
- `.gitignore` excludes raw data and secrets: `data/`, `downloads/`, `output/`, `selenium-profile/`, `*.csv`, `*.json`, `.env`. Secrets come from `.env` via python-dotenv: `GOOGLE_APPLICATION_CREDENTIALS` (path to the service-account JSON key, used by BigQuery ADC) and the Spotify API `CLIENT_ID` / `CLIENT_SECRET`. `.env` is blocked from being read by a local hook.

## Hard constraints

- The scraping step requires a human to log in manually in the Chrome window Selenium opens; this cannot be done for them. Never run `src/e_download_region_csv.py` or notebook cells that use Selenium, and never switch the driver to headless.
- To verify logic, write standalone tests or use fake data — do not run the real scraper. `src/t_clean_region_csv.py` only reads local CSVs and is safe to run.
- `src/l_load_to_bigquery.py` writes to BigQuery; don't run it unless the user asks. BigQuery uploads write only to the `spotify_data` dataset (tables `global_chart`, `korea_chart`, `japan_chart`). Do not touch any other (production) dataset.

## Chart ETL (`src/`)

`data/` at the project root is the hand-off point: E writes to it, T reads from it (both resolve it as `Path(__file__).resolve().parent.parent / "data"`). Each file's main entry function has the same name as the file.

1. **`e_download_region_csv.py`** (Extract) — `e_download_region_csv(regions, year, start_month, end_month)`. Selenium (driver found by Selenium Manager, no chromedriver path) opens charts.spotify.com, waits for manual login (`input()`), then downloads weekly CSVs to `data/`, skipping files that already exist. `get_uri()` derives chart URLs from each month's Thursdays whose date is before today (UTC); charts cover Fri–Thu and the URL is `regional-{region}-weekly/{YYYY-MM-DD}` of the Thursday. The download button is located by a brittle hashed CSS selector (`CSV_BUTTON_SELECTOR`). Errors are logged, not re-raised.
2. **`t_clean_region_csv.py`** (Transform) — `t_clean_region_csv(region, after_date)` reads `data/regional-{region}-weekly-YYYY-MM-DD.csv` files whose date is strictly later than `after_date`, in batches of `BATCH_SIZE = 10`, and returns one DataFrame with columns in `SCHEMA_COLUMNS` order:
   - `track_id` = `uri` without `spotify:track:`; `region` from the filename (`jp` / `kr` / `global`)
   - `date_interval_end` = filename date, `date_interval_start` = end − 6 days
   - `ID` (PK) = `{date_interval_end}-{rank zero-padded to 3}`, e.g. `2026-01-22-001`
   - `uploaded_at` = run time (UTC); `previous_rank = -1` (new entry) is kept as-is
   - Its `__main__` block is a commented-out test harness; the pipeline entry point is L.
3. **`l_load_to_bigquery.py`** (Load) — `l_load_to_bigquery(df, region)` loads `df` into a temporary `spotify_data._staging_<table>_<random>` table (`WRITE_TRUNCATE`), runs `MERGE ... ON ID` into the region's table (`REGION_TABLES`: `jp`→`japan_chart`, `kr`→`korea_chart`, `global`→`global_chart`; update all columns when matched, insert otherwise), then deletes the staging table in `finally`. `__main__` runs T → L for every region. Re-running is idempotent (upsert).
   - `SCHEMA` must stay identical (names and order) to T's `SCHEMA_COLUMNS` and to `spotify_weekly_chart_schema.md`, which defines the manually created tables. Tables are partitioned on `date_interval_end`; the MERGE currently has no partition filter (acceptable at current data size).

## Deprecated notebooks (`src/in_colab(deprecated)/`)

N01–N05 are the original Colab notebooks. They are no longer used and are kept only for reference — don't modify, run, or extend them; new work goes into scripts in `src/`. Quote the folder name in shell commands because of the parentheses. N01 was superseded by `src/e_download_region_csv.py`.

The notebooks ran in order N01 → N05; later notebooks depend on earlier ones via `%run` (not imports), so the notebook-global variable names are the de facto interface.

1. **N01_spotifychart_manlogin_download** — notebook version of `e_download_region_csv.py`.
2. **N02_csv_extract_trackID** — `collect_track_ids_by_region()` reads all raw chart CSVs whose filename contains `regional-{region}-weekly`, splits the `uri` column (`spotify:track:<id>`) and dedupes into sets. Exposes globals `jp_track_id_set_in_3m`, `kr_track_id_set_in_3m`, `global_track_id_set_in_3m`.
3. **N03_API_general_extract** — Client-credentials auth: `get_token()`, `get_auther_header()`, `get_spotify_data_track(url, token, sleeptime)` (sleeps to avoid 429s; returns `None` on non-200).
4. **N04_API_extract_by_trackID** (Extract) — `%run`s N02 and N03, calls `GET /v1/tracks/{id}` per track ID, and writes `{jp,kr,global}_API_data_YYYYMMDD.csv` (`utf-8-sig`, refuses to overwrite existing files). The three region blocks are copy-pasted.
5. **N05_Data_Visualization** (Transform/Analyze) — Reads the latest 2026 weekly chart CSVs, derives `uri_cleaned` and `region`, and plots source share and streams share (Top10 / 11–20 / 20–200) with matplotlib; requires Noto CJK fonts for JP/KR titles.

Notebook data directories are referenced as `<google_drive_path_to>/02_Data/01_Spotify_chart_raw` (chart CSVs) and `.../02_Spotify_API_raw` (N04 output). Colab-specific cells (`google.colab.drive.mount`, `!apt-get`, `!pip`) remain and must be replaced/skipped when running locally.

## Known issues in the current code

- N04 hardcodes `"country_chart": "JP"` in the KR and Global blocks, and uses `pandas.DataFrame` without importing `pandas` (N02 imports it as `pd`).
- N04 calls `get_token()` for every track request.
- N02 has a path marked as wrong in a comment ("路徑有錯").

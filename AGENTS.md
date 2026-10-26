# Goodbirds project guidance

Read `README.md` and `enhancements.txt` before work. Use this checkout at
`C:\github\goodbirds-site`; the OneDrive folder is not the source repository.

## Scope and sources

- Preserve existing behavior and design unless the user requests changes.
- This is a Python-generated static GitHub Pages site published from `docs/`.
- City maps: `scripts/build_map.py`; the 30-city matrix, redirects and homepage
  templates live in `.github/workflows/build.yml`.
- Coverage: `scripts/make_cities_json.py` reads that city matrix and writes
  `docs/cities.json` and `docs/cities_map.html`.
- Mega map: `scripts/build_mega_map.py`, `.github/workflows/build-mega.yml`,
  `scripts/data/ABA_Checklist.csv`, and `scripts/data/eBird_taxonomy_v2024.csv`.
- Targets: `scripts/fetch_target_species.py` fetches observations;
  `scripts/build_target_map_page.py` renders pages. Target configuration and
  index templates live in `.github/workflows/build-target-species.yml`.
- Posting: `scripts/capture_and_post_bsky.py` and the four `post-*.yml` workflows.
- Make persistent changes in generators/workflow templates. Generated HTML and
  JSON under `docs/` can be overwritten by scheduled workflows.
- Keep `/estero/` (city notable birds) separate from `/targets/estero/`.
  Targets currently include `estero`, `noord-aruba`, and
  `north-cambridge-weird-ducks` (20 waterbird species; 10 km radius; 1/5/10 km rings).
- Preserve stable city redirects and `docs/CNAME` (`goodbirds.org`).

## Local work on Windows

- Use PowerShell and run commands from the repository root.
- Use `.\.venv\Scripts\python.exe` explicitly; activation is unnecessary.
- CI uses Python 3.11. Codex's bundled Python 3.12.14 was found locally on
  October 26, 2026; the PATH `python`/`python3` entries were Store aliases and
  `py` was absent. See README for setup and the machine-specific fallback.
- Install `requirements.txt`, including PyYAML for coverage/workflow parsing
  and Windows tzdata for `ZoneInfo("America/New_York")`.
- Preview: `.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1 --directory docs`.
  Use HTTP rather than opening HTML files directly. External map assets and
  tiles still need internet. Some generated links intentionally use the live site.
- For dependency/import checks and isolated rebuild commands, follow README.
- Do not import `build_map.py` or `fetch_target_species.py` just to validate:
  these modules have top-level side effects. Compile source without executing it.
- Use ignored `.local-preview/` for scratch output. Coverage writes to `docs/`
  relative to the current directory, so run it from a scratch directory with an
  absolute `GOODBIRDS_WORKFLOW_YAML` to avoid changing published files.
- Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` for target-map
  changes. Validate Python syntax, YAML,
  JSON, matrix/output consistency, and relevant generated pages for each change.
  Browser-check visual or interactive changes. Do not treat file checks as proof
  of live API access, successful CI, or correct GitHub Pages settings.
- Target popups list every distinct checklist from retrieved sightings at the
  location, grouped by species. A shared checklist may appear under each species.
  Do not truncate to one checklist or promise a complete eBird location history.
- Optional species `group` values (`dabbler`, `diver`, `merganser`, `other`) select
  icons. Existing targets retain their circle icons. `RING_KMS` and `MAP_NOTE`
  pass explicit rings and explanatory text from the target workflow into data.
- Target updates run at 09:30 and 18:30 UTC, with a three-day lookback for the
  Weird Ducks map. Failed species requests must not overwrite prior observations.

## Credentials and posting

- Never print or commit secret values. `.env` is ignored but scripts do not
  automatically load it. Set secrets in the process environment when needed.
- `EBIRD_API_KEY` is needed for fresh observation builds.
- Set `DRY_RUN=1` for Bluesky tests. Dry runs capture screenshots without login
  or posting and do not require Bluesky credentials. Their temporary screenshot
  is deleted when the script exits.
- Playwright Chromium must be installed before screenshot tests. Credentials
  and GitHub secret availability have not been verified.

## Pending maintenance

- Mega workflow exposes `aba5_only` and `union`, but invokes `--codes 4,5`;
  script default is `aba4_5_only` and its final filter always keeps code 4/5.
  The workflow ignores its `per_species_max` input and supplies `0`. Reconcile
  these controls in a separate behavior change with explicit validation.
- Before taxonomy changes, verify latest official eBird and ABA releases.
  Use an eBird CSV with `SPECIES_CODE`; ABA rarity codes are a separate input.
  An existing weekly taxonomy reminder must not be duplicated.
- After taxonomy changes, rebuild mega outputs and inspect species-code
  matching, summary and unresolved names. Current unresolved names are
  `Little White-Tern` and `Wandering Albatross (sp.)`.
- Do not refresh live observations or publish social posts merely to validate
  documentation/setup changes.

# scripts/clean_old_maps.py
import os, pathlib, re, sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path("docs/maps")
AGE_DAYS = 7

# Matches your example: ebird_radius_map_2025-09-23_12-47-26_ET_20km.html
rx = re.compile(r"^ebird_radius_map_(\d{4}-\d{2}-\d{2})_[0-2]\d[-_][0-5]\d[-_][0-5]\d_.*\.html$")

dry_run = str(os.environ.get("DRY_RUN","false")).lower() == "true"
verbose = str(os.environ.get("VERBOSE","true")).lower() == "true"

cutoff = (datetime.now(timezone.utc) - timedelta(days=AGE_DAYS)).date()
removed = 0
matched = 0

if verbose:
    print(f"Cutoff date: {cutoff}  (delete files with date < cutoff)")
    print(f"Dry run: {dry_run}  Verbose: {verbose}")

for html in ROOT.glob("**/ebird_radius_map_*.html"):
    m = rx.match(html.name)
    if not m:
        if verbose:
            print(f"SKIP name (no match): {html.name}")
        continue
    matched += 1
    try:
        file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        if verbose:
            print(f"SKIP date parse: {html.name}")
        continue
    if file_date < cutoff:
        if verbose:
            print(f"DELETE {html} (date={file_date} < {cutoff})")
        if not dry_run:
            try:
                html.unlink()
                removed += 1
            except Exception as e:
                print(f"ERROR delete {html}: {e}", file=sys.stderr)
    else:
        if verbose:
            print(f"KEEP   {html} (date={file_date} >= {cutoff})")

print(f"Matched files: {matched}")
print(f"Removed old files: {removed}")

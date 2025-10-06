import pathlib, re
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path("docs/maps")
AGE_DAYS = 7

# Example filenames:
# ebird_radius_map_2025-09-29_06-00-00_cambridge.html
rx = re.compile(r"^ebird_radius_map_(\d{4}-\d{2}-\d{2})_[0-2]\d[-_][0-5]\d[-_][0-5]\d_.*\.html$")

cutoff = (datetime.now(timezone.utc) - timedelta(days=AGE_DAYS)).date()
removed = 0

for html in ROOT.glob("**/ebird_radius_map_*.html"):
    m = rx.match(html.name)
    if not m:
        continue
    try:
        file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        continue
    if file_date < cutoff:
        html.unlink(missing_ok=True)
        removed += 1

print(f"Removed {removed} old map files (> {AGE_DAYS} days)")

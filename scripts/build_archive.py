#!/usr/bin/env python3
import os
import pathlib
from html import escape

# Inputs from workflow env
city_slug = os.environ["CITY_SLUG"]
city_title = os.environ["CITY_TITLE"]
maps_subdir = os.environ["MAPS_SUBDIR"]
base = os.environ.get("BASE_URL", "").rstrip("/")

# Paths
docs = pathlib.Path("docs")
maps_dir = docs / "maps" / maps_subdir
out = docs / city_slug / "archive.html"

# Find built maps
items = sorted(maps_dir.glob("ebird_radius_map_*.html"))

def label(p: pathlib.Path) -> str:
    """Generate a human-friendly label from the map filename."""
    name = p.name
    try:
        core = name.split("map_")[1].split(".html")[0]
        return core.replace("_", " ")
    except Exception:
        return name

# Build list items with absolute URLs
rows = "\n".join(
    f'<li><a href="{base}/maps/{maps_subdir}/{escape(p.name)}">{escape(label(p))}</a></li>'
    for p in reversed(items)
)

# Write the archive page
out.write_text(
    f"""<!doctype html>
<meta charset="utf-8">
<title>{escape(city_title)} - Archive</title>
<h1>{escape(city_title)} - Archive</h1>
<ul>
{rows}
</ul>
""",
    encoding="utf-8",
)

print(f"Wrote archive page to {out}")

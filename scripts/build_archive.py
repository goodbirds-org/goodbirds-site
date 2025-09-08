#!/usr/bin/env python3
import os
import pathlib
from html import escape
from datetime import datetime

# Env from the workflow
city_slug = os.environ["CITY_SLUG"]
city_title = os.environ["CITY_TITLE"]
maps_subdir = os.environ["MAPS_SUBDIR"]
base = os.environ.get("BASE_URL", "").rstrip("/")
logo_basename = os.environ.get("LOGO_BASENAME", "goodbirds_logo_text.png")

# Paths
docs = pathlib.Path("docs")
maps_dir = docs / "maps" / maps_subdir
out = docs / city_slug / "archive.html"

# Collect files
items = sorted(maps_dir.glob("ebird_radius_map_*.html"))

def parse_date_from_filename(name: str) -> datetime | None:
    """
    Filenames look like: ebird_radius_map_YYYY-MM-DD_HH-MM-SS_ET_XXkm.html
    We extract the YYYY-MM-DD part.
    """
    try:
        core = name.split("map_")[1].split(".html")[0]     # YYYY-MM-DD_HH-MM-SS_ET_XXkm
        date_str = core.split("_")[0]                      # YYYY-MM-DD
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None

def label_from_filename(name: str) -> str:
    """Human label - fall back is full core if parsing fails."""
    try:
        core = name.split("map_")[1].split(".html")[0]
        return core.replace("_", " ")
    except Exception:
        return name

# Group by date (descending)
groups: dict[str, list[pathlib.Path]] = {}
for p in items:
    d = parse_date_from_filename(p.name)
    key = d.strftime("%Y-%m-%d") if d else "unknown"
    groups.setdefault(key, []).append(p)

# Sort group keys by date descending, unknown last
def sort_key(k: str):
    if k == "unknown":
        return (1, "")
    return (0, k)  # strings compare lexicographically which works for YYYY-MM-DD

ordered_keys = sorted(groups.keys(), key=sort_key, reverse=True)

# Build HTML rows grouped with <h2> per date
sections = []
for k in ordered_keys:
    files = list(reversed(sorted(groups[k])))  # newest filename last in the day list? flip if you prefer
    if k == "unknown":
        heading = "Unknown date"
    else:
        dt = datetime.strptime(k, "%Y-%m-%d")
        heading = dt.strftime("%A, %B %-d, %Y") if os.name != "nt" else dt.strftime("%A, %B %#d, %Y")
        # %-d is not on Windows. %#d works on Windows.
    links = "\n".join(
        f'<li><a href="{base}/maps/{escape(maps_subdir)}/{escape(p.name)}">{escape(label_from_filename(p.name))}</a></li>'
        for p in files
    )
    sections.append(f"<h2>{escape(heading)}</h2>\n<ul>\n{links}\n</ul>")

sections_html = "\n".join(sections)

logo_src = f"{base}/{escape(logo_basename)}"
index_href = f"{base}/"

html = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(city_title)} - Archive</title>
<style>
  :root {{
    --ink:#111; --muted:#555; --line:#e5e5e5; --bg:#fafafa; --card:#fff; --radius:14px;
  }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--ink);
               font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width: 960px; margin: 32px auto; padding: 0 18px; }}
  .brand {{ display:flex; align-items:center; gap:12px; margin-bottom: 10px; }}
  .brand img {{ height: 48px; }}
  h1 {{ font-size: 26px; margin: 6px 0 0; }}
  .nav {{ margin: 8px 0 22px; }}
  .nav a {{ text-decoration:none; color:#0a2b42; border:1px solid var(--line); background:#fff;
           padding:6px 10px; border-radius:10px; font-size:14px; }}
  h2 {{ font-size: 18px; margin: 20px 0 8px; }}
  ul {{ margin: 0 0 18px 0; padding-left: 20px; }}
  li {{ margin: 3px 0; }}
  a {{ color: inherit; }}
  a:hover {{ color: #2c7fb8; }}
</style>
<body>
  <main class="wrap">
    <header class="brand">
      <img src="{logo_src}" alt="Goodbirds">
      <div>
        <h1>{escape(city_title)} - Archive</h1>
        <div class="nav"><a href="{index_href}">Back to Cities Index</a></div>
      </div>
    </header>

    {sections_html}
  </main>
</body>
</html>
"""

out.write_text(html, encoding="utf-8")
print(f"Wrote archive page to {out}")

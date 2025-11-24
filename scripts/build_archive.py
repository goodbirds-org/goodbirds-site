#!/usr/bin/env python3
import os
import re
import pathlib
from html import escape
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Env from the workflow
city_slug = os.environ["CITY_SLUG"]
city_title = os.environ["CITY_TITLE"]
maps_subdir = os.environ["MAPS_SUBDIR"]
base = os.environ.get("BASE_URL", "").rstrip("/")
logo_basename = os.environ.get("LOGO_BASENAME", "goodbirds_logo_text.png")
days_to_show = int(os.environ.get("ARCHIVE_DAYS", "7"))

# Paths
docs = pathlib.Path("docs")
maps_dir = docs / "maps" / maps_subdir
out = docs / city_slug / "archive.html"

# Collect files
items = sorted(maps_dir.glob("ebird_radius_map_*.html"))

def parse_date_from_filename(name: str):
    """Return date from ebird_radius_map_YYYY-MM-DD_HH-MM-SS_*.html"""
    try:
        core = name.split("map_")[1].split(".html")[0]  # e.g. 2025-09-07_16-21-18_ET_15km
        date_str = core.split("_")[0]                  # 2025-09-07
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None

_time_rx = re.compile(r"_([0-2]\d)[-_]([0-5]\d)[-_]([0-5]\d)_")

def parse_time_tuple(name: str):
    """
    Return (HH, MM, SS) from filename.
    Works for both HH-MM-SS and HH_MM_SS. Returns (-1, -1, -1) if not found.
    """
    m = _time_rx.search(name)
    if not m:
        return (-1, -1, -1)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

def time_label_from_filename(name: str) -> str:
    """Return 'H:MM AM/PM' label from filename time or 'Unknown time'."""
    hh, mm, ss = parse_time_tuple(name)
    if hh == -1:
        return "Unknown time"
    dt = datetime(2000, 1, 1, hh, mm, ss)
    fmt = "%-I:%M %p" if os.name != "nt" else "%#I:%M %p"
    return dt.strftime(fmt)

# Group files by date key
groups: dict[str, list[pathlib.Path]] = {}
for p in items:
    d = parse_date_from_filename(p.name)
    key = d.isoformat() if d else "unknown"
    groups.setdefault(key, []).append(p)

# Keep only the most recent N days in ET. Drop "unknown".
today_et = datetime.now(ZoneInfo("America/New_York")).date()
min_date = today_et - timedelta(days=days_to_show - 1)
keys = [k for k in groups.keys() if k != "unknown" and k >= min_date.isoformat()]
ordered_keys = sorted(keys, reverse=True)  # newest day first

# Build sections with newest time first within each day
sections = []
for k in ordered_keys:
    files = sorted(groups[k], key=lambda p: parse_time_tuple(p.name), reverse=True)
    dt = datetime.strptime(k, "%Y-%m-%d")
    heading = dt.strftime("%A, %B %-d, %Y") if os.name != "nt" else dt.strftime("%A, %B %#d, %Y")
    links = "\n".join(
        f'<li><a href="{base}/maps/{escape(maps_subdir)}/{escape(p.name)}">{escape(time_label_from_filename(p.name))}</a></li>'
        for p in files
    )
    sections.append(f"<h2>{escape(heading)}</h2>\n<ul>\n{links}\n</ul>")

sections_html = "\n".join(sections) if sections else "<p>No maps in the past 7 days.</p>"

logo_src = f"{base}/{escape(logo_basename)}"
index_href = f"{base}/"
latest_href = f"{base}/maps/{escape(maps_subdir)}/latest.html"

html = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store, max-age=0, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta http-equiv="refresh" content="300">
<title>{escape(city_title)} - Archive</title>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-NYEBPC2JEZ"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-NYEBPC2JEZ');
</script>
<style>
  :root {{
    --ink:#111; --muted:#555; --line:#e5e5e5; --bg:#fafafa; --card:#fff; --radius:14px;
  }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--ink);
               font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width: 960px; margin: 32px auto; padding: 0 18px; }}
  .brand {{ display:flex; align-items:center; gap:12px; margin-bottom: 4px; }}
  .brand img {{ height: 108px; }}
  h1 {{ font-size: 26px; margin: 6px 0 0; }}
  .nav {{ margin: 8px 0 22px; display:flex; gap:10px; flex-wrap:wrap; }}
  .btn {{ text-decoration:none; color:#0a2b42; border:1px solid var(--line); background:#fff;
          padding:6px 10px; border-radius:10px; font-size:14px; }}
  h2 {{ font-size: 18px; margin: 20px 0 8px; }}
  ul {{ margin: 0 0 18px 0; padding-left: 20px; }}
  li {{ margin: 3px 0; }}
  a {{ color: inherit; }}
  a:hover {{ color: #2c7fb8; }}
  .note {{ color: var(--muted); font-size: 14px; margin-top: -6px; }}
</style>
<body>
  <main class="wrap">
    <header class="brand">
      <img src="{logo_src}" alt="Goodbirds">
      <div>
        <h1>{escape(city_title)} - Archive</h1>
        <div class="nav">
          <a class="btn" href="{index_href}">Back to Cities Index</a>
          <a class="btn" href="{latest_href}">Open latest map</a>
        </div>
        <p class="note">Showing the most recent {days_to_show} day(s).</p>
      </div>
    </header>

    {sections_html}
  </main>
  <script>
  (function(){{
    /* If no cache-busting param, add one once */
    if (!/[?&]t=/.test(location.search)) {{
      location.replace(location.pathname + '?t=' + Date.now());
      return;
    }}
    /* Then refresh every 5 minutes with a fresh timestamp */
    setTimeout(function () {{
      location.href = location.pathname + '?t=' + Date.now();
    }}, 300000);
  }})();
  </script>
</body>
</html>
"""

out.write_text(html, encoding="utf-8")
print(f"Wrote archive page to {out}")

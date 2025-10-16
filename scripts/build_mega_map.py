#!/usr/bin/env python3
"""
Build the Mega map HTML and summary from ABA Code-5 species and recent eBird 'notable' data.

Inputs:
  --aba_csv           Path to ABA checklist CSV (not strictly required for map if docs/mega/aba5.json exists)
  --taxonomy_csv      Path to eBird taxonomy CSV (not strictly required for map build itself)
  --out_dir           Output dir, default docs/mega
  --target_code       ABA code to select, default 5

Environment:
  EBIRD_API_KEY               required to call eBird API
  MEGA_MODE                   "aba5_only" | "union" (union may add nationally scarce notables)
  MEGA_BACK_DAYS_RECENT       integer days for recent notables (default 1)
  MEGA_BACK_DAYS_SCARCITY     integer days for scarcity window (default 365)
  MEGA_NATIONAL_MAX           cap on total markers (default 25)
  MEGA_PER_SPECIES_MAX        cap per species (default 2)

Outputs:
  docs/mega/index.html        Folium map
  docs/mega/summary.json      Build stats
  docs/mega/aba5.json         Code-5 species list (must exist already or be generated upstream)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

import requests
import folium
from folium.plugins import MarkerCluster


def getenv_int(name, default):
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def ebird_headers():
    api_key = os.getenv("EBIRD_API_KEY", "").strip()
    if not api_key:
        print("[error] EBIRD_API_KEY is missing", file=sys.stderr)
        sys.exit(2)
    return {"X-eBirdApiToken": api_key}


def load_aba5(out_dir: Path):
    p = out_dir / "aba5.json"
    if not p.exists() or p.stat().st_size == 0:
        print("[error] docs/mega/aba5.json missing or empty. Build that first.", file=sys.stderr)
        sys.exit(3)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[error] Failed to parse {p}: {e}", file=sys.stderr)
        sys.exit(3)
    if not isinstance(data, list):
        print("[error] aba5.json must be a list of species codes", file=sys.stderr)
        sys.exit(3)
    # normalize to lower
    return sorted({str(x).lower().strip() for x in data if str(x).strip()})


def fetch_recent_notables_us_ca(back_days):
    # US and CA only
    # eBird API docs: recent notable, region codes US, CA
    # We call per country so we can cap later
    base = "https://api.ebird.org/v2/data/obs/region/recent/notable"
    params = {"detail": "full", "back": back_days, "maxResults": 20000}
    out = []
    for region in ("US", "CA"):
        r = requests.get(f"{base}/{region}", headers=ebird_headers(), params=params, timeout=60)
        r.raise_for_status()
        out.extend(r.json())
    return out


def norm_species_code(s: str) -> str:
    return (s or "").strip().lower()


def pick_recent_fields(rec):
    # Select only what we render or use for tooltips
    return {
        "comName": rec.get("comName"),
        "sciName": rec.get("sciName"),
        "lat": rec.get("lat"),
        "lng": rec.get("lng"),
        "locName": rec.get("locName"),
        "obsDt": rec.get("obsDt"),
        "subId": rec.get("subId"),
        "howMany": rec.get("howMany"),
        "countryCode": rec.get("countryCode"),
        "subnational1Code": rec.get("subnational1Code"),
        "speciesCode": norm_species_code(rec.get("speciesCode")),
    }


def cap_records(records, per_species_max, national_max):
    by_species = defaultdict(list)
    for rec in records:
        sc = rec.get("speciesCode")
        if sc:
            by_species[sc].append(rec)

    # sort by observation date descending within species, keep top per_species_max
    def obs_dt_key(r):
        # r["obsDt"] example "2025-10-09 08:07"
        s = r.get("obsDt") or ""
        # keep string fallback sort
        return s

    trimmed = []
    for sc, recs in by_species.items():
        recs_sorted = sorted(recs, key=obs_dt_key, reverse=True)
        trimmed.extend(recs_sorted[:per_species_max])

    # If we exceed national_max, keep the freshest overall
    if len(trimmed) > national_max:
        trimmed = sorted(trimmed, key=obs_dt_key, reverse=True)[:national_max]

    return trimmed


def build_map_html(out_dir: Path, points):
    # Use Folium cleanly. No template injection. No stray ".{"
    m = folium.Map(
        location=[45.0, -96.0],
        zoom_start=4,
        control_scale=True,
        prefer_canvas=False,
        tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
    )

    cluster = MarkerCluster().add_to(m)

    for r in points:
        lat = r.get("lat")
        lng = r.get("lng")
        if lat is None or lng is None:
            continue
        name = r.get("comName") or "(unknown)"
        loc = r.get("locName") or ""
        when = r.get("obsDt") or ""
        sub = r.get("subId")
        href = f"https://ebird.org/checklist/{sub}" if sub else None
        popup_html = f"""
        <div>
          <b>{name}</b><br>
          {loc}<br>
          <small>{when}</small><br>
          {"<a href='" + href + "' target='_blank' rel='noopener'>Open checklist</a>" if href else ""}
        </div>
        """.strip()
        folium.CircleMarker(
            location=[lat, lng],
            radius=7,
            color="darkred",
            weight=3,
            fill=True,
            fill_color="darkred",
            fill_opacity=0.9,
        ).add_to(cluster).add_child(folium.Popup(popup_html, max_width=300)).add_child(
            folium.Tooltip(f"{name} • {loc}", sticky=True)
        )

    out_path = out_dir / "index.html"
    m.save(str(out_path))
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aba_csv", required=False, help="Path to ABA checklist CSV (not required for map if aba5.json exists)")
    ap.add_argument("--taxonomy_csv", required=False, help="Path to eBird taxonomy CSV (not required for map build)")
    ap.add_argument("--out_dir", default="docs/mega")
    ap.add_argument("--target_code", default="5")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Inputs and caps
    mode = os.getenv("MEGA_MODE", "aba5_only").strip()
    back_recent = getenv_int("MEGA_BACK_DAYS_RECENT", 1)
    back_scarcity = getenv_int("MEGA_BACK_DAYS_SCARCITY", 365)  # kept for future logic
    national_max = getenv_int("MEGA_NATIONAL_MAX", 25)
    per_species_max = getenv_int("MEGA_PER_SPECIES_MAX", 2)

    # Load ABA-5 list
    aba5_codes = load_aba5(out_dir)

    # Fetch recent notables
    raw = fetch_recent_notables_us_ca(back_recent)
    picked = [pick_recent_fields(r) for r in raw]

    # Filter by mode
    if mode == "aba5_only":
        picked = [r for r in picked if r.get("speciesCode") in aba5_codes]
    else:
        # union mode can include all notables, but still keep any that are ABA5
        # Final caps will keep the map light
        pass

    # Apply caps
    points = cap_records(picked, per_species_max=per_species_max, national_max=national_max)

    # Build map
    html_path = build_map_html(out_dir, points)

    # Summary
    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "recent_days": back_recent,
        "scarcity_days": back_scarcity,
        "national_max": national_max,
        "per_species_max": per_species_max,
        "mode": mode,
        "count_candidates": len(picked),
        "count_megas": len(points),
        "aba5_size": len(aba5_codes),
        "html_bytes": html_path.stat().st_size if html_path.exists() else 0,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Extra guard: ensure no ".{" fragment exists
    bad = False
    txt = html_path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(^|\n)\s*\.\{", txt):
        bad = True
        print("[error] Found '.{' pattern in generated HTML. This should never happen with this script.", file=sys.stderr)
    if bad:
        sys.exit(4)

    print(f"[ok] Map wrote {html_path} with {len(points)} markers")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as e:
        print(f"[error] HTTP {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(10)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

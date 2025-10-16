#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import folium
from folium.plugins import MarkerCluster, FastMarkerCluster

US_STATES = [
    "US-AL","US-AK","US-AZ","US-AR","US-CA","US-CO","US-CT","US-DE","US-FL","US-GA",
    "US-HI","US-ID","US-IL","US-IN","US-IA","US-KS","US-KY","US-LA","US-ME","US-MD",
    "US-MA","US-MI","US-MN","US-MS","US-MO","US-MT","US-NE","US-NV","US-NH","US-NJ",
    "US-NM","US-NY","US-NC","US-ND","US-OH","US-OK","US-OR","US-PA","US-RI","US-SC",
    "US-SD","US-TN","US-TX","US-UT","US-VT","US-VA","US-WA","US-WV","US-WI","US-WY",
    "US-DC","US-PR"
]
CA_PROVINCES = [
    "CA-AB","CA-BC","CA-MB","CA-NB","CA-NL","CA-NS","CA-NT","CA-NU","CA-ON","CA-PE",
    "CA-QC","CA-SK","CA-YT"
]
MAX_RESULTS = 10000  # eBird hard limit per request
FAST_CLUSTER_SWITCH = 800     # switch to FastMarkerCluster when over this many
HARD_FAIL_THRESHOLD = 2000    # fail build if more than this after caps

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
    return sorted({str(x).lower().strip() for x in data if str(x).strip()})

def pick_recent_fields(rec):
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
        "speciesCode": (rec.get("speciesCode") or "").strip().lower(),
    }

def fetch_region_notables(region, back_days):
    url = f"https://api.ebird.org/v2/data/obs/{region}/recent/notable"
    params = {"detail": "full", "back": back_days, "maxResults": MAX_RESULTS}
    r = requests.get(url, headers=ebird_headers(), params=params, timeout=60)
    if r.status_code == 400:
        print(f"[warn] 400 at {url}: {r.text[:500]}", file=sys.stderr)
    r.raise_for_status()
    return r.json()

def fetch_recent_notables_sharded(back_days, sleep_ms=120):
    out = []
    seen = set()
    regions = US_STATES + CA_PROVINCES
    for i, region in enumerate(regions, 1):
        try:
            data = fetch_region_notables(region, back_days)
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", "?")
            body = ""
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            print(f"[error] HTTP {status} for {region}: {body}", file=sys.stderr)
            continue
        for rec in data:
            key = (rec.get("subId"), (rec.get("speciesCode") or "").lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
        time.sleep(sleep_ms / 1000.0)
        if i % 10 == 0:
            print(f"[info] fetched {i}/{len(regions)} regions, cumulative {len(out)} records")
    return out

def cap_records(records, per_species_max, national_max):
    by_species = defaultdict(list)
    def obs_dt_key(r):
        return r.get("obsDt") or ""
    for rec in records:
        sc = rec.get("speciesCode")
        if sc:
            by_species[sc].append(rec)
    trimmed = []
    for sc, recs in by_species.items():
        recs_sorted = sorted(recs, key=obs_dt_key, reverse=True)
        trimmed.extend(recs_sorted[:per_species_max])
    if len(trimmed) > national_max:
        trimmed = sorted(trimmed, key=obs_dt_key, reverse=True)[:national_max]
    return trimmed

def build_map_html(out_dir: Path, points):
    # Prefer Canvas for many markers
    m = folium.Map(
        location=[45.0, -96.0],
        zoom_start=4,
        control_scale=True,
        prefer_canvas=True,
        tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
    )

    if len(points) >= FAST_CLUSTER_SWITCH:
        # Fast path for large sets. Tooltips/popups disabled for performance.
        coords = [(p["lat"], p["lng"]) for p in points if p.get("lat") is not None and p.get("lng") is not None]
        FastMarkerCluster(coords).add_to(m)
    else:
        cluster = MarkerCluster().add_to(m)
        for r in points:
            lat = r.get("lat"); lng = r.get("lng")
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
                radius=6,
                color="darkred",
                weight=2,
                fill=True,
                fill_color="darkred",
                fill_opacity=0.85,
            ).add_to(cluster).add_child(folium.Popup(popup_html, max_width=280)).add_child(
                folium.Tooltip(f"{name} • {loc}", sticky=True)
            )

    out_path = out_dir / "index.html"
    m.save(str(out_path))
    return out_path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aba_csv", required=False)
    ap.add_argument("--taxonomy_csv", required=False)
    ap.add_argument("--out_dir", default="docs/mega")
    ap.add_argument("--target_code", default="5")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mode = os.getenv("MEGA_MODE", "aba5_only").strip() or "aba5_only"
    back_recent = getenv_int("MEGA_BACK_DAYS_RECENT", 1)
    national_max = getenv_int("MEGA_NATIONAL_MAX", 60)     # tighter defaults
    per_species_max = getenv_int("MEGA_PER_SPECIES_MAX", 1)

    # Load ABA-5 codes and enforce filter by default
    aba5_codes = load_aba5(out_dir)
    aba5_set = set(aba5_codes)

    # Fetch and project
    raw = fetch_recent_notables_sharded(back_recent)
    picked = [pick_recent_fields(r) for r in raw]

    # Filter to ABA5 unless union explicitly requested
    if mode != "union":
        picked = [r for r in picked if r.get("speciesCode") in aba5_set]

    # Apply caps
    points = cap_records(picked, per_species_max=per_species_max, national_max=national_max)

    # Hard sanity checks so we never publish a monster file
    if len(points) > HARD_FAIL_THRESHOLD:
        print(f"[error] Too many points after caps: {len(points)}. Increase filters or tighten caps.", file=sys.stderr)
        sys.exit(5)

    html_path = build_map_html(out_dir, points)

    # Summary
    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "recent_days": back_recent,
        "national_max": national_max,
        "per_species_max": per_species_max,
        "mode": mode,
        "count_raw": len(raw),
        "count_candidates": len(picked),
        "count_megas": len(points),
        "aba5_size": len(aba5_codes),
        "html_bytes": html_path.stat().st_size if html_path.exists() else 0,
        "cluster_mode": "fast" if len(points) >= FAST_CLUSTER_SWITCH else "normal"
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Guard against broken HTML injection pattern
    txt = html_path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(^|\n)\s*\.\{", txt):
        print("[error] Found '.{' in generated HTML.", file=sys.stderr)
        sys.exit(4)

    print(f"[ok] Map wrote {html_path} with {len(points)} markers, cluster={summary['cluster_mode']}")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError:
        sys.exit(10)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

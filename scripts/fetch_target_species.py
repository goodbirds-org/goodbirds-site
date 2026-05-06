#!/usr/bin/env python3
"""
Fetch recent eBird observations for target species and save to a target map data directory.

Requires:
  EBIRD_API_KEY

Optional env vars:
  CENTER_LAT
  CENTER_LNG
  DIST_KM
  BACK_DAYS
  OUTPUT_DIR
  TARGET_SPECIES_JSON
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone

API_KEY = os.environ.get("EBIRD_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: EBIRD_API_KEY environment variable is not set.", file=sys.stderr)
    sys.exit(1)

CENTER_LAT = float(os.environ.get("CENTER_LAT", "26.4317"))
CENTER_LNG = float(os.environ.get("CENTER_LNG", "-81.8187"))
DIST_KM = int(os.environ.get("DIST_KM", "32"))
BACK_DAYS = int(os.environ.get("BACK_DAYS", "3"))
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "10000"))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "docs/targets/estero/data")

default_species = [
    {"code": "burowl", "name": "Burrowing Owl", "color": "#8B4513"},
    {"code": "rosspo1", "name": "Roseate Spoonbill", "color": "#FF69B4"},
    {"code": "limpki", "name": "Limpkin", "color": "#FFD700"},
    {"code": "paibun", "name": "Painted Bunting", "color": "#4169E1"},
]

species_json = os.environ.get("TARGET_SPECIES_JSON", "").strip()
if species_json:
    try:
        species_list = json.loads(species_json)
    except json.JSONDecodeError as exc:
        print(f"ERROR: TARGET_SPECIES_JSON is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
else:
    species_list = default_species

BASE_URL = "https://api.ebird.org/v2/data/obs/geo/recent"
HEADERS = {"X-eBirdApiToken": API_KEY}

all_observations = []

for species in species_list:
    code = species["code"]
    name = species["name"]
    color = species.get("color", "#666666")

    url = f"{BASE_URL}/{code}"
    params = {
        "lat": CENTER_LAT,
        "lng": CENTER_LNG,
        "dist": DIST_KM,
        "back": BACK_DAYS,
        "maxResults": MAX_RESULTS,
        "includeProvisional": "true",
    }

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        obs_list = resp.json()

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        print(f"HTTP {status} for {name}: {exc}", file=sys.stderr)
        continue
    except requests.exceptions.RequestException as exc:
        print(f"Request error for {name}: {exc}", file=sys.stderr)
        continue

    for obs in obs_list:
        obs["displayName"] = name
        obs["markerColor"] = color

    all_observations.extend(obs_list)
    print(f"{name}: {len(obs_list)} observation(s) found")

output = {
    "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "centerLat": CENTER_LAT,
    "centerLng": CENTER_LNG,
    "distKm": DIST_KM,
    "backDays": BACK_DAYS,
    "species": species_list,
    "observations": all_observations,
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
output_path = os.path.join(OUTPUT_DIR, "observations.json")

with open(output_path, "w", encoding="utf-8") as fh:
    json.dump(output, fh, indent=2, ensure_ascii=False)

print(f"Wrote {len(all_observations)} total observation(s) to {output_path}")
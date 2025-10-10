#!/usr/bin/env python3
"""
Goodbirds • Build a US+Canada Mega-Rarities map

Selection:
- MODE "aba5_only": include only sightings whose speciesCode is in docs/mega/aba5.json
- MODE "union": include sightings that are ABA-5 OR have low national count in last 365 days
  (national scarcity requires extra API calls)

Env vars:
  EBIRD_API_KEY                required
  MEGA_MODE                    "aba5_only" (default) or "union"
  MEGA_BACK_DAYS_RECENT        recent-notables window, default "1"
  MEGA_BACK_DAYS_SCARCITY      365-day window for scarcity counts, default "365"
  MEGA_NATIONAL_MAX            scarcity cutoff, default "25"
  MEGA_PER_SPECIES_MAX         cap markers per species to keep map light, default "2"
"""

import os, time, json, pathlib, requests, folium
from collections import defaultdict
from datetime import datetime, timezone

EBIRD_API = "https://api.ebird.org/v2"
HEADERS = {"X-eBirdApiToken": os.environ["EBIRD_API_KEY"]}
OUT_DIR = pathlib.Path("docs/mega"); OUT_DIR.mkdir(parents=True, exist_ok=True)

MODE                 = os.environ.get("MEGA_MODE", "aba5_only").strip().lower()
BACK_DAYS_RECENT     = int(os.environ.get("MEGA_BACK_DAYS_RECENT", "1"))
BACK_DAYS_SCARCITY   = int(os.environ.get("MEGA_BACK_DAYS_SCARCITY", "365"))
NATIONAL_MAX         = int(os.environ.get("MEGA_NATIONAL_MAX", "25"))
PER_SPECIES_MAX      = int(os.environ.get("MEGA_PER_SPECIES_MAX", "2"))
COUNTRIES            = ["US", "CA"]

# Read ABA-5 list of speciesCodes, if present
ABA5_PATH = OUT_DIR / "aba5.json"
aba5_codes = set()
if ABA5_PATH.exists():
    try:
        aba5_codes = set(json.loads(ABA5_PATH.read_text(encoding="utf-8")))
    except Exception:
        aba5_codes = set()

def get_json(url, params=None, sleep=0.25):
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=30)
    if r.status_code == 429:
        time.sleep(1.0)
        return get_json(url, params, sleep)
    r.raise_for_status()
    time.sleep(sleep)
    return r.json()

def recent_notable(country):
    return get_json(
        f"{EBIRD_API}/data/obs/{country}/recent/notable",
        {"back": BACK_DAYS_RECENT, "detail": "full"}
    )

def national_species_count(species_code):
    total = 0
    for c in COUNTRIES:
        obs = get_json(f"{EBIRD_API}/data/obs/{c}/recent/{species_code}",
                       {"back": BACK_DAYS_SCARCITY})
        total += len(obs)
    return total

print(f"[info] MODE={MODE} recent={BACK_DAYS_RECENT}d scarcity={BACK_DAYS_SCARCITY}d cutoff={NATIONAL_MAX} per_species_max={PER_SPECIES_MAX} aba5_size={len(aba5_codes)}")

# 1) fetch recent notables for US and CA
notes = []
for c in COUNTRIES:
    try:
        notes.extend(recent_notable(c))
    except Exception as e:
        print(f"[warn] failed fetching {c} notables: {e}")

# 2) de-dupe by obsId or subId|speciesCode
seen, candidates = set(), []
for o in notes:
    key = o.get("obsId") or f"{o.get('subId')}|{o.get('speciesCode')}"
    if key in seen: 
        continue
    seen.add(key)
    candidates.append(o)

print(f"[info] candidates={len(candidates)}")

# 3) scarcity counts only if needed
national_counts = {}
if MODE != "aba5_only":
    species_codes = sorted({o.get("speciesCode") for o in candidates if o.get("speciesCode")})
    for sc in species_codes:
        try:
            national_counts[sc] = national_species_count(sc)
        except Exception as e:
            print(f"[warn] scarcity count failed for {sc}: {e}")
            national_counts[sc] = 999999

# 4) select megas
megas = []
for o in candidates:
    sc = o.get("speciesCode")
    if not sc:
        continue
    if MODE == "aba5_only":
        if sc in aba5_codes:
            megas.append(o)
    else:
        ncount = national_counts.get(sc, 999999)
        if sc in aba5_codes or ncount <= NATIONAL_MAX:
            megas.append(o)

# 5) cap markers per species to keep HTML small
if PER_SPECIES_MAX > 0:
    newest_by_species = defaultdict(list)
    # sort newest first by obsDt string
    for o in sorted(megas, key=lambda x: x.get("obsDt", ""), reverse=True):
        sc = o.get("speciesCode")
        if len(newest_by_species[sc]) < PER_SPECIES_MAX:
            newest_by_species[sc].append(o)
    megas = [o for lst in newest_by_species.values() for o in lst]

print(f"[info] megas_after_cap={len(megas)}")

# 6) build map
m = folium.Map(
    location=[45, -96],
    zoom_start=4,
    tiles="OpenStreetMap",
    control_scale=True,
    width="100%",
    height="600px",
)
try:
    from folium.plugins import MarkerCluster
    layer = MarkerCluster(name="Megas").add_to(m)
except Exception:
    layer = m

def color_for(n, aba=False):
    if aba: return "purple"
    if n <= 5: return "darkred"
    if n <= 10: return "red"
    if n <= 25: return "orange"
    return "gray"

for o in megas:
    lat, lng = o.get("lat"), o.get("lng")
    if lat is None or lng is None:
        continue
    sp  = o.get("comName", "Unknown")
    sc  = o.get("speciesCode", "")
    loc = o.get("locName", "Unknown location")
    dt  = o.get("obsDt", "")
    sub = o.get("subId")
    chk = f"https://ebird.org/checklist/{sub}" if sub else "https://ebird.org/home"
    ncount = 0 if MODE == "aba5_only" else int(national_counts.get(sc, 0))
    aba = sc in aba5_codes
    note = " • ABA Code-5" if aba else ""
    html = f"""
    <div>
      <b>{sp}</b>{note}<br>
      {loc}<br>
      <small>{dt}</small><br>
      <small>US+CA 365-day obs: {ncount}</small><br>
      <a href="{chk}" target="_blank" rel="noopener">Open checklist</a>
    </div>
    """
    folium.CircleMarker(
        location=[lat, lng],
        radius=7,
        color=color_for(ncount, aba),
        fill=True,
        fill_opacity=0.9,
        popup=folium.Popup(html, max_width=300),
        tooltip=f"{sp} • {loc}",
    ).add_to(layer)

folium.LayerControl().add_to(m)

# 7) save artifacts
m.save(OUT_DIR / "index.html")
summary = {
    "built_at_utc": datetime.now(timezone.utc).isoformat(),
    "mode": MODE,
    "recent_days": BACK_DAYS_RECENT,
    "scarcity_days": BACK_DAYS_SCARCITY,
    "national_max": NATIONAL_MAX,
    "per_species_max": PER_SPECIES_MAX,
    "aba5_size": len(aba5_codes),
    "count_candidates": len(candidates),
    "count_megas": len(megas),
}
(OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print("[done] wrote docs/mega/index.html and summary.json")

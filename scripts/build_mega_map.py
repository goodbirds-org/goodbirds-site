#!/usr/bin/env python3
import os, time, json, pathlib, requests, folium
from datetime import datetime, timezone

EBIRD_API = "https://api.ebird.org/v2"
HEADERS = {"X-eBirdApiToken": os.environ["EBIRD_API_KEY"]}
OUT_DIR = pathlib.Path("docs/mega"); OUT_DIR.mkdir(parents=True, exist_ok=True)

BACK_DAYS_RECENT   = int(os.environ.get("MEGA_BACK_DAYS_RECENT", "2"))
BACK_DAYS_SCARCITY = int(os.environ.get("MEGA_BACK_DAYS_SCARCITY", "365"))
NATIONAL_MAX       = int(os.environ.get("MEGA_NATIONAL_MAX", "25"))
COUNTRIES = ["US", "CA"]

ABA5_PATH = OUT_DIR / "aba5.json"
aba5_codes = set()
if ABA5_PATH.exists():
    try:
        aba5_codes = set(json.loads(ABA5_PATH.read_text()))
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
    return get_json(f"{EBIRD_API}/data/obs/{country}/recent/notable",
                    {"back": BACK_DAYS_RECENT, "detail": "full"})

def national_species_count(species_code):
    total = 0
    for c in COUNTRIES:
        try:
            obs = get_json(f"{EBIRD_API}/data/obs/{c}/recent/{species_code}",
                           {"back": BACK_DAYS_SCARCITY})
            total += len(obs)
        except Exception:
            pass
    return total

print(f"[info] Building mega map — recent={BACK_DAYS_RECENT}d scarcity={BACK_DAYS_SCARCITY}d cutoff={NATIONAL_MAX}")

notes = []
for c in COUNTRIES:
    try:
        notes.extend(recent_notable(c))
    except Exception as e:
        print(f"[warn] notable {c} failed: {e}")

seen, candidates = set(), []
for o in notes:
    key = o.get("obsId") or f"{o.get('subId')}|{o.get('speciesCode')}"
    if key not in seen:
        seen.add(key)
        candidates.append(o)

species_codes = sorted({o.get("speciesCode") for o in candidates if o.get("speciesCode")})
national_counts = {}
for sc in species_codes:
    try:
        national_counts[sc] = national_species_count(sc)
    except Exception as e:
        print(f"[warn] count {sc} failed: {e}")
        national_counts[sc] = 999999

megas = []
for o in candidates:
    sc = o.get("speciesCode")
    if not sc:
        continue
    ncount = national_counts.get(sc, 999999)
    if ncount <= NATIONAL_MAX or sc in aba5_codes:
        megas.append(o)

print(f"[info] candidates={len(candidates)} megas={len(megas)}")

m = folium.Map(location=[45, -96], zoom_start=4, tiles="CartoDB positron", control_scale=True)
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
    sp = o.get("comName", "Unknown")
    sc = o.get("speciesCode", "")
    loc = o.get("locName", "Unknown location")
    dt  = o.get("obsDt", "")
    sub = o.get("subId")
    chk = f"https://ebird.org/checklist/{sub}" if sub else "https://ebird.org/home"
    ncount = national_counts.get(sc, 0)
    aba = sc in aba5_codes
    extra = " • ABA Code-5" if aba else ""
    html = f"""
    <div>
      <b>{sp}</b>{extra}<br>
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

m.save(OUT_DIR / "index.html")
summary = {
    "built_at_utc": datetime.now(timezone.utc).isoformat(),
    "recent_days": BACK_DAYS_RECENT,
    "scarcity_days": BACK_DAYS_SCARCITY,
    "national_max": NATIONAL_MAX,
    "count_candidates": len(candidates),
    "count_megas": len(megas),
    "aba5_size": len(aba5_codes),
}
(OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
print("[done] wrote docs/mega/index.html and summary.json")

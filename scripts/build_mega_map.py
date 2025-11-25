
#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import unicodedata
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import folium
import requests
from folium.plugins import MarkerCluster, Fullscreen, LocateControl, MousePosition

# --------------------- Region sharding ---------------------

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
MAX_RESULTS = 10000
HARD_FAIL_THRESHOLD = 20000

# --------------------- Small utils ---------------------

def getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default

def ebird_headers() -> Dict[str, str]:
    api_key = os.getenv("EBIRD_API_KEY", "").strip()
    if not api_key:
        print("[error] EBIRD_API_KEY is missing", file=sys.stderr)
        sys.exit(2)
    return {"X-eBirdApiToken": api_key}

# --------------------- Name normalization ---------------------

PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
SPACES_RE = re.compile(r"\s+")

def normalize_name(s: Optional[str]) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).strip().lower()
    s = re.sub(r"\([^)]*\)", "", s)
    s = PUNCT_RE.sub(" ", s)
    s = SPACES_RE.sub(" ", s).strip()
    return s

def expand_variants(name: Optional[str]) -> List[str]:
    if not name:
        return []
    base = str(name)
    variants = {
        normalize_name(base),
        normalize_name(base.replace("-", " ")),
        normalize_name(base.replace("/", " ")),
        normalize_name(base.replace(",", " ")),
        normalize_name(re.sub(r"'s\b", "s", base)),
        normalize_name(re.sub(r"'s\b", "", base)),
        normalize_name(re.sub(r"[^\w\s]", " ", base)),
    }
    v = [normalize_name(base)] + [x for x in variants if x != normalize_name(base)]
    return [x for x in v if x]

# --------------------- ABA + Taxonomy mapping ---------------------

ABA_CODE_KEYS = {
    "abachecklistcode","abacode","code","abraritycode","raritycode","aba checklist code","aba rarity code","aba code"
}
ABA_NAME_KEYS = {
    "primary_com_name","common name","english name","name","primary com name","primary common name"
}

def detect_column(header: List[str], want: Set[str]) -> Optional[str]:
    norm = {h.strip().lower(): h for h in header}
    for k in want:
        if k in norm:
            return norm[k]
    return None

def load_allowed_species_codes(
    aba_csv: Path,
    taxonomy_csv: Path,
    out_dir: Path,
    target_codes: Set[int]
) -> Tuple[Set[str], Dict[int, int]]:
    if not aba_csv.exists() or not taxonomy_csv.exists():
        print("[error] ABA or taxonomy CSV not found", file=sys.stderr)
        sys.exit(3)

    with taxonomy_csv.open(encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        tax_rows = list(reader)

    tax_header = list(tax_rows[0].keys()) if tax_rows else []
    name_cand = None
    for cand in ["PRIMARY_COM_NAME","PRIMARY COM NAME","ENGLISH_NAME","English Name","Common Name"]:
        if cand in tax_header:
            name_cand = cand
            break
    spc_col = "SPECIES_CODE"
    if name_cand is None or spc_col not in tax_header:
        upper_map = {c.upper(): c for c in tax_header}
        name_cand = upper_map.get("PRIMARY_COM_NAME") or upper_map.get("ENGLISH_NAME")
        spc_col = upper_map.get("SPECIES_CODE", spc_col)
        if not name_cand or not spc_col:
            print("[error] Could not find taxonomy columns", file=sys.stderr)
            sys.exit(3)

    name_to_code = {}
    for r in tax_rows:
        nm = normalize_name(r.get(name_cand))
        sc = (r.get(spc_col) or "").strip().lower()
        if nm and sc:
            name_to_code[nm] = sc

    raw = aba_csv.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = None
    delim = ","

    def looks_like_header(line: str, sep: str) -> bool:
        toks = [t.strip().strip('"').lower() for t in line.split(sep)]
        if len(toks) < 3:
            return False
        has_code = any("code" in t for t in toks)
        has_name = any("name" in t for t in toks)
        return has_code and has_name

    for i, line in enumerate(raw[:50]):
        if line.count(",") >= 2 and looks_like_header(line, ","):
            header_idx = i; delim = ","; break
    if header_idx is None:
        for i, line in enumerate(raw[:50]):
            if line.count("\t") >= 2 and looks_like_header(line, "\t"):
                header_idx = i; delim = "\t"; break
    if header_idx is None:
        print("[error] Could not locate header row in ABA checklist", file=sys.stderr)
        sys.exit(3)

    rdr = csv.DictReader(raw[header_idx:], delimiter=delim)
    code_col = detect_column(rdr.fieldnames, ABA_CODE_KEYS) or next(iter(rdr.fieldnames), None)
    name_col = detect_column(rdr.fieldnames, ABA_NAME_KEYS)
    if not code_col or not name_col:
        print(f"[error] Could not detect ABA columns. Header: {rdr.fieldnames}", file=sys.stderr)
        sys.exit(3)

    per_code_map: Dict[int, Set[str]] = {c: set() for c in target_codes}
    code_matcher = re.compile(r"^\s*(\d)\b")
    unresolved = []

    for r in rdr:
        raw_code = str(r.get(code_col, "")).strip()
        m = code_matcher.match(raw_code)
        if not m:
            continue
        code_val = int(m.group(1))
        if code_val not in target_codes:
            continue
        nm = r.get(name_col)
        sc = None
        for v in expand_variants(nm):
            sc = name_to_code.get(v)
            if sc:
                break
        if sc:
            per_code_map[code_val].add(sc)
        else:
            unresolved.append(nm)

    out_dir.mkdir(parents=True, exist_ok=True)
    allowed: Set[str] = set()
    for c in sorted(target_codes):
        codes_sorted = sorted(per_code_map[c])
        (out_dir / f"aba{c}.json").write_text(json.dumps(codes_sorted, indent=2), encoding="utf-8")
        allowed.update(codes_sorted)
    (out_dir / "aba_allowed.json").write_text(json.dumps(sorted(allowed), indent=2), encoding="utf-8")
    if 5 in target_codes:
        (out_dir / "aba5.json").write_text(json.dumps(sorted(per_code_map[5]), indent=2), encoding="utf-8")

    counts = {c: len(per_code_map[c]) for c in sorted(target_codes)}
    if unresolved:
        with (out_dir / "unresolved_names.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f); w.writerow(["UNRESOLVED_COMMON_NAME"])
            for nm in sorted(set(unresolved)):
                w.writerow([nm])
    return allowed, counts

# --------------------- eBird fetching ---------------------

def pick_recent_fields(rec: dict) -> dict:
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

def fetch_region_notables(region: str, back_days: int) -> List[dict]:
    url = f"https://api.ebird.org/v2/data/obs/{region}/recent/notable"
    params = {"detail": "full", "back": back_days, "maxResults": MAX_RESULTS}
    r = requests.get(url, headers=ebird_headers(), params=params, timeout=60)
    if r.status_code == 400:
        print(f"[warn] 400 at {url}: {r.text[:500]}", file=sys.stderr)
    r.raise_for_status()
    return r.json()

def fetch_recent_notables_sharded(back_days: int, sleep_ms: int = 120) -> List[dict]:
    out: List[dict] = []
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

# --------------------- Helper: load ABA code4/5 sets ---------------------

def _load_json_list(p: Path) -> Set[str]:
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return set()

def load_code_sets(preferred_dir: Path) -> Tuple[Set[str], Set[str]]:
    cand4 = [preferred_dir / "aba4.json", Path("docs/mega/aba4.json"), Path("aba4.json")]
    cand5 = [preferred_dir / "aba5.json", Path("docs/mega/aba5.json"), Path("aba5.json")]
    code4 = set()
    code5 = set()
    for c in cand4:
        if c.exists():
            code4 = _load_json_list(c); break
    for c in cand5:
        if c.exists():
            code5 = _load_json_list(c); break
    return ({s.lower() for s in code4}, {s.lower() for s in code5})

# --------------------- Map UI helpers ---------------------

def build_info_ui(map_title: str, logo_src: str, recent_days: int) -> str:
    # Eastern time, same style as map pages
    eastern_now = datetime.now(ZoneInfo("America/New_York"))
    built_str = eastern_now.strftime("Built: %b %d, %Y %I:%M %p %Z")
    legend = """
      <div style='margin-top:8px'>
        <div style='display:flex; align-items:center; gap:8px; margin:4px 0'>
          <span style='display:inline-block; width:14px; height:14px; border-radius:50%; background:#f1c40f; border:1.5px solid #222;'></span>
          <span>ABA Code 4</span>
        </div>
        <div style='display:flex; align-items:center; gap:8px; margin:4px 0'>
          <span style='display:inline-block; width:14px; height:14px; border-radius:50%; background:#d32f2f; border:1.5px solid #222;'></span>
          <span>ABA Code 5</span>
        </div>
      </div>
    """
    html = f"""
    <style>
      .gb-info-btn {{
        position: fixed; left: 16px; bottom: 16px; width: 44px; height: 44px; border-radius: 50%;
        background: #ffffff; border: 1px solid #999; box-shadow: 0 2px 6px rgba(0,0,0,0.25);
        z-index: 1201; display: flex; align-items: center; justify-content: center;
        font: 700 18px/1 system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
        cursor: pointer; user-select: none;
      }}
      .gb-info-btn:focus {{ outline: 2px solid #2c7fb8; }}
      .gb-info-panel {{
        position: fixed; left: 16px; bottom: 70px; z-index: 1200;
        background: rgba(255,255,255,0.98); border: 1px solid #999; border-radius: 10px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.3); padding: 12px; width: min(92vw, 360px);
        max-height: 70vh; display: none;
      }}
      .gb-info-header {{ display: grid; grid-template-columns: auto 1fr; grid-gap: 12px; align-items: center; }}
      .gb-info-title {{ font-weight: 700; font-size: 16px; margin: 0; }}
      .gb-info-meta {{ font-size: 13px; margin-top: 2px; }}
    </style>

    <div class="gb-info-btn" id="gbInfoBtn" role="button" aria-label="Show map info" aria-expanded="false">i</div>

    <div class="gb-info-panel" id="gbInfoPanel" aria-hidden="true">
      <div class="gb-info-header">
        <div><img src='{logo_src}' alt='Goodbirds logo' style='height:100px;display:block;'></div>
        <div>
          <h3 class="gb-info-title">{map_title}</h3>
          <div class="gb-info-meta">eBird Notable - last {recent_days} day(s)</div>
          <div class="gb-info-meta">{built_str}</div>
          {legend}
        </div>
      </div>
    </div>

    <script>
      (function(){{
        var btn = document.getElementById('gbInfoBtn');
        var panel = document.getElementById('gbInfoPanel');
        function openPanel(){{ panel.style.display='block'; btn.setAttribute('aria-expanded','true'); panel.setAttribute('aria-hidden','false'); }}
        function closePanel(){{ panel.style.display='none'; btn.setAttribute('aria-expanded','false'); panel.setAttribute('aria-hidden','true'); }}
        btn.addEventListener('click', function(){{ panel.style.display==='block' ? closePanel() : openPanel(); }});
        document.addEventListener('click', function(e){{ if(!panel.contains(e.target) && e.target!==btn) closePanel(); }});
      }})();
    </script>
    """
    return html

def guess_logo_src() -> str:
    # Absolute URL for GitHub Pages deployment
    return "https://goodbirds-org.github.io/goodbirds-site/goodbirds_logo_text.png"

# --------------------- Capping and map building ---------------------

def cap_records(records: List[dict], per_species_max: int, national_max: int) -> List[dict]:
    by_species: Dict[str, List[dict]] = defaultdict(list)
    def obs_dt_key(r: dict) -> str:
        return r.get("obsDt") or ""
    for rec in records:
        sc = rec.get("speciesCode")
        if sc:
            by_species[sc].append(rec)
    trimmed: List[dict] = []
    for sc, recs in by_species.items():
        recs_sorted = sorted(recs, key=obs_dt_key, reverse=True)
        if per_species_max <= 0:
            trimmed.extend(recs_sorted)
        else:
            trimmed.extend(recs_sorted[:per_species_max])
    if national_max > 0 and len(trimmed) > national_max:
        trimmed = sorted(trimmed, key=obs_dt_key, reverse=True)[:national_max]
    return trimmed

def build_map_html(out_dir: Path, points: List[dict], code4: Set[str], code5: Set[str], recent_days: int, map_title: str) -> Path:
    m = folium.Map(
        location=[45.0, -96.0],
        zoom_start=4,
        control_scale=True,
        prefer_canvas=True,
        tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
    )

    Fullscreen().add_to(m)
    LocateControl(auto_start=False, keepCurrentZoomLevel=False).add_to(m)
    MousePosition(separator=" , ", prefix="Lat, Lon:").add_to(m)

    logo_src = guess_logo_src()
    m.get_root().html.add_child(folium.Element(build_info_ui(map_title, logo_src, recent_days)))

    # Group by (lat,lng,species) and aggregate all checklists
    agg = defaultdict(list)
    meta_for_key = {}
    for r in points:
        lat = r.get("lat"); lng = r.get("lng")
        sc = (r.get("speciesCode") or "").lower()
        if lat is None or lng is None or not sc:
            continue
        key = (round(lat, 6), round(lng, 6), sc)
        agg[key].append(r)
        if key not in meta_for_key:
            meta_for_key[key] = {
                "comName": r.get("comName") or "(unknown)",
                "locName": r.get("locName") or "",
                "code": 4 if sc in code4 else 5 if sc in code5 else None,
            }

    cluster = MarkerCluster().add_to(m)
    for key, recs in agg.items():
        lat, lng, sc = key
        meta = meta_for_key[key]
        code = meta["code"]
        if code not in (4, 5):
            # Safety: do not draw anything that is not explicitly code 4 or 5
            continue

        com_name = meta["comName"]
        loc_name = meta["locName"]

        # dedupe checklists
        seen = set()
        items = []
        for rec in sorted(recs, key=lambda x: x.get("obsDt") or "", reverse=True):
            cid = rec.get("subId")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            dt = rec.get("obsDt") or ""
            how_many = rec.get("howMany")
            count_txt = f" ({how_many})" if how_many not in (None, "Unknown") else ""
            items.append(f"<li><a href='https://ebird.org/checklist/{cid}' target='_blank' rel='noopener'>Checklist</a> – {dt}{count_txt}</li>")
        lst = "<ul style='margin:6px 0 0 16px; padding:0;'>" + "".join(items) + "</ul>" if items else "<div>No checklists.</div>"

        popup_html = (
            "<div style='font-size:13px;'>"
            f"<div><b>{com_name}</b> – ABA Code {code}</div>"
            f"<div><b>Location:</b> {loc_name}</div>"
            "<div style='margin-top:6px; font-weight:600;'>Checklists:</div>"
            f"{lst}</div>"
        )

        if code == 4:
            bg = "#f1c40f"
        else:
            bg = "#d32f2f"

        icon = folium.DivIcon(html=f"<div style='width:14px;height:14px;border-radius:50%;background:{bg};border:1.5px solid #222;'></div>",
                              icon_size=(14, 14), icon_anchor=(7, 7))

        folium.Marker([lat, lng], icon=icon, tooltip=com_name,
                      popup=folium.Popup(popup_html, max_width=320)).add_to(cluster)

    out_path = out_dir / "index.html"
    m.save(str(out_path))
    return out_path

# --------------------- Main ---------------------

def main():
    ap = argparse.ArgumentParser(description="Build Mega map with ABA Code 4 and 5, multi-checklist popups, and info legend")
    ap.add_argument("--aba_csv", required=True, help="Path to ABA checklist CSV")
    ap.add_argument("--taxonomy_csv", required=True, help="Path to eBird taxonomy CSV")
    ap.add_argument("--out_dir", default="docs/mega", help="Output directory")
    ap.add_argument("--codes", default="4,5", help="Comma-separated ABA codes to include, e.g. '5' or '4,5'")
    ap.add_argument("--map_title", default="Goodbirds Mega Map", help="Title shown in the info panel")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mode = os.getenv("MEGA_MODE", "aba4_5_only").strip() or "aba4_5_only"
    back_recent = getenv_int("MEGA_BACK_DAYS_RECENT", 2)
    national_max = getenv_int("MEGA_NATIONAL_MAX", 0)
    per_species_max = getenv_int("MEGA_PER_SPECIES_MAX", 0)

    try:
        target_codes = {int(x.strip()) for x in args.codes.split(",") if x.strip()}
    except Exception:
        print("[error] --codes must be a comma-separated list of integers like '5' or '4,5'", file=sys.stderr)
        sys.exit(2)

    allowed_set, per_code_counts = load_allowed_species_codes(
        Path(args.aba_csv), Path(args.taxonomy_csv), out_dir, target_codes
    )
    allowed_set = {s.lower() for s in allowed_set}

    # Load ABA code sets for coloring and final filtering
    code4_set, code5_set = load_code_sets(out_dir)
    if not code4_set and 4 in target_codes:
        print("[error] aba4.json did not load or is empty. Aborting to avoid '?' markers.", file=sys.stderr)
        sys.exit(6)
    if not code5_set and 5 in target_codes:
        print("[error] aba5.json did not load or is empty. Aborting to avoid '?' markers.", file=sys.stderr)
        sys.exit(6)

    raw = fetch_recent_notables_sharded(back_recent)
    picked = [pick_recent_fields(r) for r in raw]

    # Filter to allowed species unless union mode is requested
    if mode != "union":
        picked = [r for r in picked if (r.get("speciesCode") or "").lower() in allowed_set]

    # Hard filter: keep ONLY code 4 or 5 species to guarantee no unknowns
    code45_union = code4_set | code5_set
    picked = [r for r in picked if (r.get("speciesCode") or "").lower() in code45_union]

    points = cap_records(picked, per_species_max=per_species_max, national_max=national_max)

    if 0 < HARD_FAIL_THRESHOLD < len(points):
        print(f"[error] Too many points after caps: {len(points)} - tighten filters or caps", file=sys.stderr)
        sys.exit(5)

    html_path = build_map_html(out_dir, points, code4_set, code5_set, back_recent, args.map_title)

    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "recent_days": back_recent,
        "national_max": national_max,
        "per_species_max": per_species_max,
        "mode": mode,
        "requested_codes": sorted(list(target_codes)),
        "per_code_counts": per_code_counts,
        "count_raw": len(raw),
        "count_candidates": len(picked),
        "count_megas": len(points),
        "html_bytes": html_path.stat().st_size if html_path.exists() else 0,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    txt = html_path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(^|\n)\s*\.\{", txt):
        print("[error] Found '.{' in generated HTML", file=sys.stderr)
        sys.exit(4)

    print(f"[ok] Map wrote {html_path} with {len(points)} aggregated markers - codes={sorted(list(target_codes))}")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as e:
        sys.exit(10)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

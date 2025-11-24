#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import folium
import requests
from folium.plugins import FastMarkerCluster, MarkerCluster

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
MAX_RESULTS = 10000  # eBird hard limit per request
FAST_CLUSTER_SWITCH = 800
HARD_FAIL_THRESHOLD = 2000

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
    s = re.sub(r"\([^)]*\)", "", s)  # drop parentheticals
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
    """
    Read ABA checklist, pick rows whose code column starts with any of target_codes,
    map common names to eBird SPECIES_CODE via taxonomy, and write per-code JSONs plus merged set.
    Returns the merged allowed set, and a small count dict per code.
    """
    if not aba_csv.exists() or not taxonomy_csv.exists():
        print("[error] ABA or taxonomy CSV not found", file=sys.stderr)
        sys.exit(3)

    # Load taxonomy mapping: normalized primary common name -> species_code
    with taxonomy_csv.open(encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        tax_rows = list(reader)
    # find columns
    tax_header = list(tax_rows[0].keys()) if tax_rows else []
    name_cand = None
    for cand in ["PRIMARY_COM_NAME","PRIMARY COM NAME","ENGLISH_NAME","English Name","Common Name"]:
        if cand in tax_header:
            name_cand = cand
            break
    spc_col = "SPECIES_CODE"
    if name_cand is None or spc_col not in tax_header:
        # try case-insensitive fallback
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

    # Load ABA checklist and detect columns
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

    # Build sets per code and merged
    per_code_map: Dict[int, Set[str]] = {c: set() for c in target_codes}
    code_matcher = re.compile(r"^\s*(\d)\b")  # accepts 4, 4*, 4?, 4.0 etc.
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

    # Write per-code and merged JSONs
    out_dir.mkdir(parents=True, exist_ok=True)
    allowed: Set[str] = set()
    for c in sorted(target_codes):
        codes_sorted = sorted(per_code_map[c])
        (out_dir / f"aba{c}.json").write_text(json.dumps(codes_sorted, indent=2), encoding="utf-8")
        allowed.update(codes_sorted)
    # keep old filename for backward compatibility
    (out_dir / "aba_allowed.json").write_text(json.dumps(sorted(allowed), indent=2), encoding="utf-8")
    if 5 in target_codes:
        (out_dir / "aba5.json").write_text(json.dumps(sorted(per_code_map[5]), indent=2), encoding="utf-8")

    counts = {c: len(per_code_map[c]) for c in sorted(target_codes)}
    if unresolved:
        # optional debug list
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
        trimmed.extend(recs_sorted[:per_species_max])
    if len(trimmed) > national_max:
        trimmed = sorted(trimmed, key=obs_dt_key, reverse=True)[:national_max]
    return trimmed

def build_map_html(out_dir: Path, points: List[dict]) -> Path:
    m = folium.Map(
        location=[45.0, -96.0],
        zoom_start=4,
        control_scale=True,
        prefer_canvas=True,
        tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
    )
    if len(points) >= FAST_CLUSTER_SWITCH:
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

# --------------------- Main ---------------------

def main():
    ap = argparse.ArgumentParser(description="Build Mega map with ABA Code filters")
    ap.add_argument("--aba_csv", required=True, help="Path to ABA checklist CSV")
    ap.add_argument("--taxonomy_csv", required=True, help="Path to eBird taxonomy CSV")
    ap.add_argument("--out_dir", default="docs/mega", help="Output directory")
    ap.add_argument("--codes", default="5", help="Comma-separated ABA codes to include, e.g. '5' or '4,5'")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Inputs and caps via env
    mode = os.getenv("MEGA_MODE", "aba5_only").strip() or "aba5_only"
    back_recent = getenv_int("MEGA_BACK_DAYS_RECENT", 1)
    national_max = getenv_int("MEGA_NATIONAL_MAX", 60)
    per_species_max = getenv_int("MEGA_PER_SPECIES_MAX", 1)

    # Build allowed species list from ABA + taxonomy
    try:
        target_codes = {int(x.strip()) for x in args.codes.split(",") if x.strip()}
    except Exception:
        print("[error] --codes must be a comma-separated list of integers like '5' or '4,5'", file=sys.stderr)
        sys.exit(2)
    allowed_set, per_code_counts = load_allowed_species_codes(
        Path(args.aba_csv), Path(args.taxonomy_csv), out_dir, target_codes
    )
    allowed_set = set(allowed_set)  # species codes, lowercase

    # Fetch notables sharded
    raw = fetch_recent_notables_sharded(back_recent)
    picked = [pick_recent_fields(r) for r in raw]

    # Filter to allowed species unless union mode is requested
    if mode != "union":
        picked = [r for r in picked if r.get("speciesCode") in allowed_set]

    # Apply caps
    points = cap_records(picked, per_species_max=per_species_max, national_max=national_max)

    # Hard guard
    if len(points) > HARD_FAIL_THRESHOLD:
        print(f"[error] Too many points after caps: {len(points)} - tighten filters or caps", file=sys.stderr)
        sys.exit(5)

    # Build map
    html_path = build_map_html(out_dir, points)

    # Summary
    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "recent_days": back_recent,
        "national_max": national_max,
        "per_species_max": per_species_max,
        "mode": mode,
        "requested_codes": sorted(list(target_codes)),
        "per_code_counts": per_code_counts,  # how many species matched in ABA per code
        "count_raw": len(raw),
        "count_candidates": len(picked),
        "count_megas": len(points),
        "html_bytes": html_path.stat().st_size if html_path.exists() else 0,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Guard against malformed HTML injection
    txt = html_path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(^|\n)\s*\.\{", txt):
        print("[error] Found '.{' in generated HTML", file=sys.stderr)
        sys.exit(4)

    print(f"[ok] Map wrote {html_path} with {len(points)} markers - codes={sorted(list(target_codes))}")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.HTTPError as e:
        # fetch function already logs details
        sys.exit(10)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

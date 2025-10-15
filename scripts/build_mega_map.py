#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict

import pandas as pd

# ---------- Normalization helpers ----------

_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_SPACES_RE = re.compile(r"\s+")

def normalize_name(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = re.sub(r"\([^)]*\)", "", s)  # drop parentheticals
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACES_RE.sub(" ", s).strip()
    return s

def expand_variants(name: str) -> List[str]:
    if name is None:
        return []
    base = str(name)
    v = set()
    v.add(base)
    nb = normalize_name(base)
    v.add(nb)
    v.add(normalize_name(base.replace("-", " ")))
    v.add(normalize_name(re.sub(r"'s\b", "s", base)))
    v.add(normalize_name(re.sub(r"'s\b", "", base)))
    v.add(normalize_name(base.replace("/", " ")))
    v.add(normalize_name(base.replace(",", " ")))
    v.add(normalize_name(re.sub(r"[^\w\s]", " ", base)))
    ordered = []
    for cand in [nb] + [c for c in v if c != nb]:
        if cand and cand not in ordered:
            ordered.append(cand)
    return ordered

# ---------- CSV loaders with optional preamble trim ----------

def read_csv_with_header_detection(path: Path) -> pd.DataFrame:
    """
    Reads a CSV even if a preamble exists above the header line.
    We scan the first ~50 lines to find a header that contains likely fields.
    """
    likely_headers = [
        ("Common Name", "ABA Checklist Code"),
        ("PRIMARY_COM_NAME", "SPECIES_CODE"),
        ("PRIMARY COM NAME", "SPECIES_CODE"),
    ]

    # First try straight read
    try:
        return pd.read_csv(path)
    except Exception:
        pass

    # Scan for a header row
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    for i in range(min(50, len(lines))):
        header = [h.strip() for h in lines[i].split(",")]
        header_upper = [h.upper() for h in header]
        for a, b in likely_headers:
            if a.upper() in header_upper and b.upper() in header_upper:
                # re-read from this line as header
                return pd.read_csv(path, header=i)

    # Fallback: let pandas try again (will raise)
    return pd.read_csv(path)

# ---------- Column detection ----------

def detect_aba_columns(df: pd.DataFrame) -> Tuple[str, str]:
    cols_map: Dict[str, str] = {
        re.sub(r"[^A-Z0-9]+", "", str(c).upper()): c for c in df.columns
    }
    NAME_KEYS = {
        "PRIMARYCOMNAME", "PRIMARYCOMMONNAME", "PRIMARYCOMNAMEEN",
        "COMMONNAME", "ENGLISHNAME", "NAME"
    }
    CODE_KEYS = {
        "ABACHECKLISTCODE", "ABACODE", "CODE",
        "ABARARITYCODE", "RARITYCODE"
    }

    name_col = next((cols_map[k] for k in NAME_KEYS if k in cols_map), None)
    code_col = next((cols_map[k] for k in CODE_KEYS if k in cols_map), None)

    if not name_col:
        for cand in ["Common Name", "PRIMARY_COM_NAME", "English Name", "Primary Com Name"]:
            if cand in df.columns:
                name_col = cand
                break
    if not code_col:
        for cand in ["ABA Checklist Code", "ABA Rarity Code", "Rarity Code", "ABA Code"]:
            if cand in df.columns:
                code_col = cand
                break

    if not name_col:
        # heuristic: column with many values that contain spaces
        cands = [c for c in df.columns if df[c].astype(str).str.contains(" ").mean() > 0.5]
        if cands:
            name_col = cands[0]

    if not code_col:
        # heuristic: column that looks like small integers 1..6
        cands = []
        for c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            uniq = set(v.dropna().astype(int).unique().tolist())
            if v.notna().mean() > 0.5 and uniq.issubset({1, 2, 3, 4, 5, 6}):
                cands.append(c)
        if cands:
            code_col = cands[0]

    if not name_col or not code_col:
        print("[error] Could not locate Name and ABA Code columns after normalization", file=sys.stderr)
        print("[debug] Raw header:", list(df.columns), file=sys.stderr)
        print("[debug] Normalized header:", list(cols_map.keys()), file=sys.stderr)
        raise SystemExit(2)

    return name_col, code_col

def detect_taxonomy_columns(tax_df: pd.DataFrame) -> Tuple[str, str]:
    name_cand = None
    for cand in ["PRIMARY_COM_NAME", "PRIMARY COM NAME", "ENGLISH_NAME", "Common Name"]:
        if cand in tax_df.columns:
            name_cand = cand
            break
    if name_cand is None:
        for c in tax_df.columns:
            if tax_df[c].astype(str).str.contains(" ").mean() > 0.5:
                name_cand = c
                break

    spc = "SPECIES_CODE"
    if spc not in tax_df.columns:
        alt = None
        for c in tax_df.columns:
            key = c.upper().replace(" ", "_")
            if key in {"SPECIES_CODE", "SPECIESCODE", "EBIRD_CODE", "EBIRDCODE"}:
                alt = c
                break
        if alt is None:
            print("[error] Taxonomy CSV does not have SPECIES_CODE", file=sys.stderr)
            raise SystemExit(4)
        tax_df = tax_df.rename(columns={alt: spc})

    if name_cand is None:
        print("[error] Could not find a taxonomy common-name column", file=sys.stderr)
        raise SystemExit(3)

    return name_cand, spc, tax_df

# ---------- Core mapping ----------

def build_codes(
    aba_df: pd.DataFrame,
    tax_df: pd.DataFrame,
    name_col: str,
    code_col: str,
    target_code: str
):
    tax_df = tax_df.copy()
    tax_name_col, spc_col, tax_df = detect_taxonomy_columns(tax_df)
    tax_df["_norm"] = tax_df[tax_name_col].astype(str).map(normalize_name)
    name_to_code = dict(zip(tax_df["_norm"], tax_df[spc_col].astype(str).str.lower()))

    rows, unmatched = [], []
    for _, r in aba_df.iterrows():
        nm = r.get(name_col)
        code_val = str(r.get(code_col) or "").strip()
        variants = expand_variants(nm)
        sc = None
        for v in variants:
            sc = name_to_code.get(v)
            if sc:
                break
        rows.append({
            "ABA_ROW_NAME": nm,
            "ABA_CODE": code_val,
            "MATCH_VARIANT": variants[0] if variants else None,
            "SPECIES_CODE": sc,
        })
        if sc is None and nm not in (None, ""):
            unmatched.append(str(nm))

    res = pd.DataFrame(rows)

    # accept 5, 5?, 5*, 5.0 etc.
    sel_mask = res["ABA_CODE"].astype(str).str.match(rf"^\s*{re.escape(str(target_code))}\b", na=False)
    sel = res[sel_mask]
    codes = sorted(set(sel["SPECIES_CODE"].dropna().astype(str).str.lower().tolist()))
    return codes, res, unmatched

# ---------- IO helpers ----------

def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")

def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0

# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Build ABA Code-N species list and diagnostics for the Mega map.")
    ap.add_argument("--aba_csv", required=True, help="Path to ABA checklist CSV")
    ap.add_argument("--taxonomy_csv", required=True, help="Path to eBird taxonomy CSV")
    ap.add_argument("--out_dir", default="docs/mega", help="Output directory for artifacts")
    ap.add_argument("--target_code", default="5", help="ABA code to select")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    aba_csv = Path(args.aba_csv)
    tax_csv = Path(args.taxonomy_csv)

    if not aba_csv.exists():
        print(f"[error] ABA CSV not found: {aba_csv}", file=sys.stderr)
        sys.exit(5)
    if not tax_csv.exists():
        print(f"[error] Taxonomy CSV not found: {tax_csv}", file=sys.stderr)
        sys.exit(6)

    # Load with preamble-resilient reader
    aba_df = read_csv_with_header_detection(aba_csv)
    tax_df = read_csv_with_header_detection(tax_csv)

    name_col, code_col = detect_aba_columns(aba_df)
    print(f"[info] Using ABA name column: {name_col}")
    print(f"[info] Using ABA code column: {code_col}")

    codes, resolve_df, unmatched = build_codes(
        aba_df, tax_df, name_col=name_col, code_col=code_col, target_code=str(args.target_code)
    )

    # Write artifacts
    out_dir.mkdir(parents=True, exist_ok=True)
    abaN_json_path = out_dir / "aba5.json"  # keep filename for downstream
    write_json(codes, abaN_json_path)
    write_csv(resolve_df, out_dir / "resolve.csv")
    write_csv(pd.DataFrame(sorted(set(unmatched)), columns=["UNRESOLVED_COMMON_NAME"]),
              out_dir / "unresolved_names.csv")

    summary = {
        "aba_csv": str(aba_csv),
        "taxonomy_csv": str(tax_csv),
        "out_dir": str(out_dir),
        "target_code": str(args.target_code),
        "counts": {
            "aba_rows": int(len(aba_df)),
            "resolved_rows": int((resolve_df["SPECIES_CODE"].notna()).sum()),
            "unresolved_rows": int((resolve_df["SPECIES_CODE"].isna()).sum()),
            "selected_code_rows": int(resolve_df["ABA_CODE"].astype(str).str.match(rf"^\s*{re.escape(str(args.target_code))}\b", na=False).sum()),
            "species_codes_emitted": int(len(codes)),
        },
        "sizes": {
            "aba5_json": file_size(abaN_json_path),
            "resolve_csv": file_size(out_dir / "resolve.csv"),
            "unresolved_names_csv": file_size(out_dir / "unresolved_names.csv"),
        },
    }
    write_json(summary, out_dir / "summary.json")

    if len(codes) == 0:
        print("[error] No species codes emitted for the selected ABA code", file=sys.stderr)
        sys.exit(7)

    print(f"[ok] wrote {len(codes)} species codes to {abaN_json_path}")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as e:
        raise
    except Exception as ex:
        print(f"[error] {ex}", file=sys.stderr)
        sys.exit(1)

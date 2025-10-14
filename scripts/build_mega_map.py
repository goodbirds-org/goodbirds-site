#!/usr/bin/env python3
"""
Build docs/mega/aba5.json from an ABA checklist CSV and the eBird taxonomy CSV.

Works with headers like:
  ['Common Name', 'Scientific Name', 'Banding Code', 'ABA Checklist Code']

Also accepts variants like PRIMARY_COM_NAME or ABA_CODE.

Usage:
  python scripts/prepare_aba5.py \
    --aba data/ABA_Checklist.csv \
    --tax data/eBird_taxonomy_v2024.csv \
    --out docs/mega/aba5.json \
    --report artifacts/aba_match_report.csv \
    --suggest artifacts/aba_unmatched_suggestions.csv
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
from difflib import get_close_matches


def norm_text(s: str) -> str:
    if s is None:
        return ""
    s2 = " ".join(str(s).strip().split())
    s2 = unicodedata.normalize("NFKD", s2).encode("ascii", "ignore").decode("ascii")
    return s2


def expand_variants(name: str):
    # "Common Name (AOS Name)" -> ["Common Name", "AOS Name"]
    if not name:
        return []
    s = str(name).strip()
    out = []
    main = re.split(r"\s*\(", s)[0].strip()
    if main:
        out.append(norm_text(main))
    for m in re.finditer(r"\(([^)]+)\)", s):
        alt = m.group(1).strip()
        if alt:
            out.append(norm_text(alt))
    # de-dupe, keep order
    seen, dedup = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup


def normcol(c: str) -> str:
    # Uppercase and strip non-alphanumerics for robust header matching
    return re.sub(r"[^A-Z0-9]+", "", str(c).upper())


def read_csv_with_preamble_trim(path: Path) -> pd.DataFrame:
    # First try a plain read
    try:
        return pd.read_csv(path, dtype=str)
    except Exception:
        pass

    # If that fails, scan first lines to find a likely header row
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = None
    sep = ","
    for i, line in enumerate(lines[:200]):
        parts_tab = "\t" in line and "," not in line
        parts = line.split("\t") if parts_tab else line.split(",")
        parts = [p.strip().strip('"') for p in parts]
        keys = {normcol(p) for p in parts}
        if any(k in keys for k in {"PRIMARYCOMNAME", "COMMONNAME", "ENGLISHNAME", "NAME"}) and \
           any(k in keys for k in {"ABACHECKLISTCODE", "ABACODE", "CODE"}):
            header_idx = i
            sep = "\t" if parts_tab else ","
            break

    if header_idx is None:
        # fallback: let pandas sniff
        return pd.read_csv(path, dtype=str, engine="python", sep=None)

    delimiter = "TAB" if sep == "\t" else "COMMA"
    print(f"[info] Header found on line {header_idx+1} using delimiter {delimiter}")
    return pd.read_csv(path, dtype=str, header=header_idx, sep=sep, engine="python")


def detect_aba_columns(df: pd.DataFrame):
    cols_map = {normcol(c): c for c in df.columns}

    # Accept your exact headers and common variants
    NAME_KEYS = {"PRIMARYCOMNAME", "COMMONNAME", "ENGLISHNAME", "NAME"}
    CODE_KEYS = {"ABACHECKLISTCODE", "ABACODE", "CODE"}

    name_col = next((cols_map[k] for k in NAME_KEYS if k in cols_map), None)
    code_col = next((cols_map[k] for k in CODE_KEYS if k in cols_map), None)

    # Fallback for your shown header strings
    if not name_col and "Common Name" in df.columns:
        name_col = "Common Name"
    if not code_col and "ABA Checklist Code" in df.columns:
        code_col = "ABA Checklist Code"

    # Heuristic fallback: texty column for name, numeric 1..6 column for code
    if not name_col:
        text_candidates = []
        for c in df.columns:
            vals = df[c].dropna().astype(str).head(50).tolist()
            if any(" " in v for v in vals):
                text_candidates.append(c)
        if text_candidates:
            name_col = text_candidates[0]

    if not code_col:
        numeric_candidates = []
        for c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            uniq = set(v.dropna().astype(int).unique().tolist())
            if v.notna().mean() > 0.7 and uniq.issubset({1, 2, 3, 4, 5, 6}):
                numeric_candidates.append(c)
        if numeric_candidates:
            code_col = numeric_candidates[0]

    if not name_col or not code_col:
        print("[error] Could not locate Name and ABA Code columns after normalization")
        print("[debug] Raw header:", list(df.columns))
        print("[debug] Normalized header:", list(cols_map.keys()))
        raise SystemExit(1)

    return name_col, code_col


def load_taxonomy(tax_path: Path) -> pd.DataFrame:
    tax = pd.read_csv(tax_path, dtype=str)
    req = {"PRIMARY_COM_NAME", "SPECIES_CODE"}
    if not req.issubset(set(tax.columns)):
        missing = sorted(req - set(tax.columns))
        print(f"[error] Taxonomy missing columns: {missing}")
        raise SystemExit(1)
    tax["_norm_name"] = tax["PRIMARY_COM_NAME"].map(norm_text)
    return tax


def build_codes(aba_df: pd.DataFrame, tax_df: pd.DataFrame, name_col: str, code_col: str, target_code: str):
    name_to_code = dict(zip(tax_df["_norm_name"], tax_df["SPECIES_CODE"].str.lower()))

    rows = []
    unmatched = []
    for _, r in aba_df.iterrows():
        nm = r.get(name_col)
        code_val = r.get(code_col)
        variants = expand_variants(nm)
        sc = None
        for v in variants:
            sc = name_to_code.get(v)
            if sc:
                break
        rows.append({
            "ABA_ROW_NAME": nm,
            "ABA_CODE": str(code_val) if code_val is not None else None,
            "MATCH_VARIANT": variants[0] if variants else None,
            "SPECIES_CODE": sc,
        })
        if sc is None and nm not in (None, ""):
            unmatched.append(nm)

    res = pd.DataFrame(rows)
    sel = res[res["ABA_CODE"].astype(str).str.strip() == target_code] if "ABA_CODE" in res.columns else res
    codes = sorted(set(sel["SPECIES_CODE"].dropna().tolist()))
    return codes, res, unmatched


def write_suggestions(unmatched, tax_df: pd.DataFrame, path: Path):
    tax_names = tax_df["PRIMARY_COM_NAME"].dropna().unique().tolist()
    tax_norm = [" ".join(n.strip().split()).lower() for n in tax_names]
    inv_norm = dict(zip(tax_norm, tax_names))
    sugg_rows = []
    for name in sorted(set(unmatched)):
        n = " ".join(str(name).strip().split()).lower()
        if not n:
            continue
        for m in get_close_matches(n, tax_norm, n=3, cutoff=0.7):
            sugg_rows.append({"ABA_ROW_NAME": name, "Suggested_MATCH": inv_norm[m]})
    pd.DataFrame(sugg_rows).drop_duplicates().to_csv(path, index=False, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aba", required=True, type=Path, help="ABA checklist CSV")
    ap.add_argument("--tax", required=True, type=Path, help="eBird taxonomy CSV")
    ap.add_argument("--out", required=True, type=Path, help="Output aba5.json")
    ap.add_argument("--report", type=Path, default=None, help="Optional: write match report CSV")
    ap.add_argument("--suggest", type=Path, default=None, help="Optional: write fuzzy suggestions CSV")
    ap.add_argument("--code", default="5", help="ABA code to select (default 5)")
    args = ap.parse_args()

    aba_df = read_csv_with_preamble_trim(args.aba)
    tax_df = load_taxonomy(args.tax)

    name_col, code_col = detect_aba_columns(aba_df)
    print(f"[info] Using name column: {name_col} | code column: {code_col}")

    codes, report_df, unmatched = build_codes(aba_df, tax_df, name_col, code_col, args.code)

    # Write outputs
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(codes, indent=2), encoding="utf-8")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report_df.to_csv(args.report, index=False, encoding="utf-8")

    if args.suggest:
        args.suggest.parent.mkdir(parents=True, exist_ok=True)
        write_suggestions(unmatched, tax_df, args.suggest)

    print(f"[ok] matched {report_df['SPECIES_CODE'].notna().sum()} of {len(report_df)} rows")
    print(f"[ok] wrote {len(codes)} Code-{args.code} species codes to {args.out}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        sys.exit(e.code)
    except Exception as ex:
        print(f"[error] {ex}")
        sys.exit(1)

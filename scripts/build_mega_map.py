#!/usr/bin/env python3
"""
Build docs/mega/aba5.json from an ABA checklist CSV and the eBird taxonomy CSV.

Input expectations (flexible, robust to headers and preambles):
- ABA checklist has a name column (e.g., "Common Name") and a numeric code column
  (e.g., "ABA Checklist Code"), where 5 indicates Code-5.
- Taxonomy has PRIMARY_COM_NAME and SPECIES_CODE.

Usage:
  python scripts/prepare_aba5.py \
    --aba data/ABA_Checklist.csv \
    --tax data/eBird_taxonomy_v2024.csv \
    --out docs/mega/aba5.json

Optional:
  --report artifacts/aba_match_report.csv
  --suggest artifacts/aba_unmatched_suggestions.csv
  --code 5
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
    """'Common Name (AOS Name)' -> ['Common Name', 'AOS Name'] with normalization."""
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
    """Uppercase and strip non-alphanumerics for robust header matching."""
    return re.sub(r"[^A-Z0-9]+", "", str(c).upper())


def read_csv_with_preamble_trim(path: Path):
    """
    Try normal read. If that fails to find useful headers, scan first ~100 rows
    to locate a likely header row containing a name and an ABA code column.
    """
    # First pass: let pandas infer
    try:
        df = pd.read_csv(path, dtype=str)
        return df
    except Exception:
        pass

    # Second pass: scan lines for a header
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = None
    for i, line in enumerate(text[:150]):
        # naive CSV split by comma; if tabs are present, split on tabs
        parts = line.split("\t") if "\t" in line and "," not in line else line.split(",")
        parts = [p.strip().strip('"') for p in parts]
        keys = {normcol(p) for p in parts}
        if any(k in keys for k in {"PRIMARYCOMNAME", "COMMONNAME", "ENGLISHNAME", "NAME"}) and \
           any(k in keys for k in {"ABACHECKLISTCODE", "ABACODE", "CODE"}):
            header_idx = i
            break

    if header_idx is None:
        # fallback: return best-effort read and let later logic try to detect columns
        return pd.read_csv(path, dtype=str, engine="python", sep=None)

    # Delimiter guess from the header row
    raw = text[header_idx]
    sep = "\t" if "\t" in raw and "," not in raw else ","

    # Safe logging without backslash in f-string
    delimiter = "TAB" if sep == "\t" else "COMMA"
    print(f"[info] Header found on line {header_idx+1} using delimiter {delimiter}")

    # Re-read from that row as header
    df = pd.read_csv(path, dtype=str, header=header_idx, sep=sep, engine="python")
    return df


def detect_aba_columns(df: pd.DataFrame):
    cols_map = {normcol(c): c for c in df.columns}

    name_keys = {"PRIMARYCOMNAME", "COMMONNAME", "ENGLISHNAME", "NAME", "COMMONNAMEAOS"}
    code_keys = {"ABACHECKLISTCODE", "ABACODE", "CODE"}

    name_col = next((cols_map[k] for k in name_keys if k in cols_map), None)
    code_col = next((cols_map[k] for k in code_keys if k in cols_map), None)

    # If not found, try to guess: pick a texty column for name and a numeric 1..6 column for code
    if not name_col:
        text_candidates = []
        for c in df.columns:
            vals = df[c].dropna().astype(str).head(40).tolist()
            if any(" " in v for v in vals):
                text_candidates.append(c)
        if text_candidates:
            name_col = text_candidates[0]

    if not code_col:
        numeric_candidates = []
        for c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            uniq = set(v.dropna().astype(int).unique().tolist())
            if v.notna().mean() > 0.75 and uniq.issubset({1, 2, 3, 4, 5, 6}):
                numeric_candidates.append(c)
        if numeric_candidates:
            code_col = numeric_candidates[0]

    if not name_col or not code_col:
        print("[error] Could not locate Name and ABA Code columns after trimming and normalization")
        print("[debug] Raw header:", list(df.columns))
        print("[debug] Normalized header:", list(cols_map.keys()))
        raise SystemExit(1)

    return name_col, code_col


def load_taxonomy(tax_path: Path):
    tax = pd.read_csv(tax_path, dtype=str)
    req = {"PRIMARY_COM_NAME", "SPECIES_CODE"}
    if not req.issubset(set(tax.columns)):
        missing = sorted(req - set(tax.columns))
        print(f"[error] Taxonomy missing columns: {missing}")
        raise SystemExit(1)
    tax["_norm_name"] = tax["PRIMARY_COM_NAME"].map(norm_text)
    return tax


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aba", required=True, type=Path, help="ABA checklist CSV")
    ap.add_argument("--tax", required=True, type=Path, help="eBird taxonomy CSV")
    ap.add_argument("--out", required=True, type=Path, help="Output aba5.json path")
    ap.add_argument("--report", type=Path, default=None, help="Optional: write a CSV match report")
    ap.add_argument("--suggest", type=Path, default=None, help="Optional: write fuzzy suggestions CSV")
    ap.add_argument("--code", default="5", help="ABA code to select (default 5)")
    args = ap.parse_args()

    # Read inputs
    aba_df = read_csv_with_preamble_trim(args.aba)
    tax_df = load_taxonomy(args.tax)

    # Detect columns
    name_col, code_col = detect_aba_columns(aba_df)
    print(f"[info] Using name column: {name_col} | code column: {code_col}")

    # Build lookup from taxonomy
    name_to_code = dict(zip(tax_df["_norm_name"], tax_df["SPECIES_CODE"].str.lower()))

    # Match ABA names to species codes
    rows = []
    unmatched = []
    for _, r in aba_df.iterrows():
        nm_raw = r.get(name_col)
        code_raw = r.get(code_col)
        variants = expand_variants(nm_raw)
        found = None
        for v in variants:
            found = name_to_code.get(v)
            if found:
                break
        rows.append(
            {
                "ABA_ROW_NAME": nm_raw,
                "ABA_CODE": str(code_raw) if code_raw is not None else None,
                "MATCH_VARIANT": variants[0] if variants else None,
                "SPECIES_CODE": found,
            }
        )
        if found is None and nm_raw not in (None, ""):
            unmatched.append(nm_raw)

    res = pd.DataFrame(rows)

    # Filter to requested code
    sel = res[res["ABA_CODE"].astype(str).str.strip() == args.code] if "ABA_CODE" in res.columns else res
    codes = sorted(set(sel["SPECIES_CODE"].dropna().tolist()))

    # Write outputs
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(codes, indent=2), encoding="utf-8")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(args.report, index=False, encoding="utf-8")

    if args.suggest:
        # simple fuzzy suggestions for unmatched names
        tax_names = tax_df["PRIMARY_COM_NAME"].dropna().unique().tolist()
        tax_norm = [" ".join(n.strip().split()).lower() for n in tax_names]
        inv_norm = dict(zip(tax_norm, tax_names))
        sugg_rows = []
        for name in sorted(set(unmatched)):
            n = " ".join(str(name).strip().split()).lower()
            if not n:
                continue
            matches = get_close_matches(n, tax_norm, n=3, cutoff=0.7)
            for m in matches:
                sugg_rows.append({"ABA_ROW_NAME": name, "Suggested_MATCH": inv_norm[m]})
        pd.DataFrame(sugg_rows).drop_duplicates().to_csv(args.suggest, index=False, encoding="utf-8")

    print(f"[ok] matched {res['SPECIES_CODE'].notna().sum()} of {len(res)} rows")
    print(f"[ok] wrote {len(codes)} Code-{args.code} species codes to {args.out}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        sys.exit(e.code)
    except Exception as ex:
        print(f"[error] {ex}")
        sys.exit(1)

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
    """
    Normalize a common name for matching.
    - lowercase
    - strip parentheticals
    - remove punctuation
    - collapse spaces
    """
    if s is None:
        return ""
    s = str(s).strip()
    # remove parentheticals - e.g., "X (Y subspecies)" -> "X"
    s = re.sub(r"\([^)]*\)", "", s)
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACES_RE.sub(" ", s).strip()
    return s


def expand_variants(name: str) -> List[str]:
    """
    Generate reasonable matching variants for a given common name.
    Try to help with small formatting differences.
    """
    if name is None:
        return []
    base = str(name)
    v = set()

    # raw
    v.add(base)

    # normalized base
    nb = normalize_name(base)
    v.add(nb)

    # try swapping hyphen and space in common hyphenated names like "kittlitz's murrelet"
    hy_swap = base.replace("-", " ")
    v.add(normalize_name(hy_swap))

    # drop possessives visually - e.g., "kittlitz's" -> "kittlitzs" and "kittlitz"
    v.add(normalize_name(re.sub(r"'s\b", "s", base)))
    v.add(normalize_name(re.sub(r"'s\b", "", base)))

    # compress slashes or " / "
    v.add(normalize_name(base.replace("/", " ")))

    # remove commas
    v.add(normalize_name(base.replace(",", " ")))

    # greedy punctuation strip
    v.add(normalize_name(re.sub(r"[^\w\s]", " ", base)))

    # de-duplicate and keep a stable order with normalized first
    ordered = []
    for cand in [nb] + [c for c in v if c != nb]:
        if cand and cand not in ordered:
            ordered.append(cand)
    return ordered


# ---------- Detection of columns ----------

def detect_aba_columns(df: pd.DataFrame) -> Tuple[str, str]:
    """
    Try to find the columns for Common Name and ABA Code.
    Accepts a variety of header spellings and falls back to heuristics.
    """
    # build normalized header map
    cols_map: Dict[str, str] = {
        re.sub(r"[^A-Z0-9]+", "", str(c).upper()): c for c in df.columns
    }

    NAME_KEYS = {
        "PRIMARYCOMNAME", "COMMONNAME", "ENGLISHNAME", "NAME",
        "PRIMARYCOMMONNAME", "PRIMARYCOMNAMEEN"
    }
    CODE_KEYS = {
        "ABACHECKLISTCODE", "ABACODE", "CODE",
        "ABARARITYCODE", "RARITYCODE"
    }

    name_col = next((cols_map[k] for k in NAME_KEYS if k in cols_map), None)
    code_col = next((cols_map[k] for k in CODE_KEYS if k in cols_map), None)

    # common human-readable fallbacks
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

    # heuristic fallback - guess which column looks like a name
    if not name_col:
        text_candidates = []
        for c in df.columns:
            vals = df[c].dropna().astype(str).head(50).tolist()
            # look for spaces in values - common names usually have at least two words
            if any(" " in v for v in vals):
                text_candidates.append(c)
        if text_candidates:
            name_col = text_candidates[0]

    # heuristic fallback - guess which column looks like a small integer code 1..6
    if not code_col:
        numeric_candidates = []
        for c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce")
            uniq = set(v.dropna().astype(int).unique().tolist())
            if v.notna().mean() > 0.5 and uniq.issubset({1, 2, 3, 4, 5, 6}):
                numeric_candidates.append(c)
        if numeric_candidates:
            code_col = numeric_candidates[0]

    if not name_col or not code_col:
        print("[error] Could not locate Name and ABA Code columns after normalization", file=sys.stderr)
        print("[debug] Raw header:", list(df.columns), file=sys.stderr)
        print("[debug] Normalized header:", list(cols_map.keys()), file=sys.stderr)
        raise SystemExit(2)

    return name_col, code_col


# ---------- Core mapping ----------

def build_codes(
    aba_df: pd.DataFrame,
    tax_df: pd.DataFrame,
    name_col: str,
    code_col: str,
    target_code: str
) -> Tuple[List[str], pd.DataFrame, List[str]]:
    """
    Map ABA rows to eBird species codes and select those matching the target code.
    Returns:
      - sorted unique species codes for the target code
      - full resolve DataFrame
      - list of unmatched common names
    """
    # prepare taxonomy map from normalized PRIMARY_COM_NAME -> SPECIES_CODE
    tax_df = tax_df.copy()
    # taxonomy header variants - be permissive but prefer eBird canonical headers
    tax_name_col = None
    for cand in ["PRIMARY_COM_NAME", "PRIMARY COM NAME", "PRIMARY_COM_NAME_EN", "PRIMARY_COM_NAME_FR", "ENGLISH_NAME", "COMMON NAME"]:
        if cand in tax_df.columns:
            tax_name_col = cand
            break
    if tax_name_col is None:
        # find first column that looks like names
        for c in tax_df.columns:
            if tax_df[c].astype(str).str.contains(" ").mean() > 0.5:
                tax_name_col = c
                break
    if tax_name_col is None:
        print("[error] Could not find a taxonomy common-name column", file=sys.stderr)
        raise SystemExit(3)

    if "SPECIES_CODE" not in tax_df.columns:
        # look for likely species code column if header changed
        spc_col = None
        for c in tax_df.columns:
            if c.upper().replace(" ", "_") in {"SPECIES_CODE", "SPECIESCODE", "EBIRD_CODE", "EBIRDCODE"}:
                spc_col = c
                break
        if spc_col is None:
            print("[error] Taxonomy CSV does not have SPECIES_CODE", file=sys.stderr)
            raise SystemExit(4)
        tax_df = tax_df.rename(columns={spc_col: "SPECIES_CODE"})

    tax_df["_norm_name"] = tax_df[tax_name_col].astype(str).map(normalize_name)
    name_to_code = dict(zip(tax_df["_norm_name"], tax_df["SPECIES_CODE"].astype(str).str.lower()))

    # resolve ABA rows
    rows = []
    unmatched = []
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

    # Accept 5, 5*, 5?, 5.0, etc - same idea as your workflow
    sel_mask = res["ABA_CODE"].astype(str).str.match(rf"^\s*{re.escape(str(target_code))}\b", na=False)
    sel = res[sel_mask]
    codes = sorted(set(sel["SPECIES_CODE"].dropna().astype(str).str.lower().tolist()))
    return codes, res, unmatched


# ---------- IO utilities ----------

def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Build ABA Code-5 species list and diagnostics for the Mega map.")
    ap.add_argument("--aba_csv", required=True, help="Path to ABA checklist CSV")
    ap.add_argument("--taxonomy_csv", required=True, help="Path to eBird taxonomy CSV (eBird v2024 works)")
    ap.add_argument("--out_dir", default="docs/mega", help="Output directory for artifacts")
    ap.add_argument("--target_code", default="5", help="ABA code to select - default 5")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    aba_csv = Path(args.aba_csv)
    tax_csv = Path(args.taxonomy_csv)

    if not aba_csv.exists():
        print(f"[error] ABA CSV not found: {aba_csv}", file=sys.stderr)
        return 5
    if not tax_csv.exists():
        print(f"[error] Taxonomy CSV not found: {tax_csv}", file=sys.stderr)
        return 6

    # Load
    aba_df = pd.read_csv(aba_csv)
    tax_df = pd.read_csv(tax_csv)

    # Detect columns
    name_col, code_col = detect_aba_columns(aba_df)
    print(f"[info] Using ABA name column: {name_col}")
    print(f"[info] Using ABA code column: {code_col}")

    # Build codes and diagnostics
    codes, resolve_df, unmatched = build_codes(
        aba_df, tax_df, name_col=name_col, code_col=code_col, target_code=str(args.target_code)
    )

    # Write artifacts
    aba5_json_path = out_dir / "aba5.json"
    write_json(codes, aba5_json_path)
    print(f"[info] Wrote {aba5_json_path} with {len(codes)} species codes")

    resolve_csv_path = out_dir / "resolve.csv"
    write_csv(resolve_df, resolve_csv_path)
    print(f"[info] Wrote {resolve_csv_path} - full row-by-row match report")

    unresolved_csv_path = out_dir / "unresolved_names.csv"
    if unmatched:
        unresolved_df = pd.DataFrame(sorted(set(unmatched)), columns=["UNRESOLVED_COMMON_NAME"])
        write_csv(unresolved_df, unresolved_csv_path)
        print(f"[warn] {len(unresolved_df)} names did not map to a species code - see {unresolved_csv_path}")
    else:
        # still emit an empty file so downstream steps can read it
        write_csv(pd.DataFrame(columns=["UNRESOLVED_COMMON_NAME"]), unresolved_csv_path)
        print("[info] All ABA names resolved to species codes")

    # Summary - helps you debug CI logs at a glance
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
            "aba5_json": file_size(aba5_json_path),
            "resolve_csv": file_size(resolve_csv_path),
            "unresolved_names_csv": file_size(unresolved_csv_path),
        },
    }
    summary_json_path = out_dir / "summary.json"
    write_json(summary, summary_json_path)
    print(f"[info] Wrote {summary_json_path}")

    # Failure guard - if we ended up with zero codes, exit nonzero so CI flags it
    if len(codes) == 0:
        print("[error] No species codes emitted for the selected ABA code. Check column detection, code values, and unresolved names.", file=sys.stderr)
        return 7

    return 0


if __name__ == "__main__":
    sys.exit(main())

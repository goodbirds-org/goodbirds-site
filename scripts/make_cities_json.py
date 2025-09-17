#!/usr/bin/env python3
import json, os, sys, pathlib
try:
    import yaml  # PyYAML
except ImportError:
    print("PyYAML not installed. pip install pyyaml", file=sys.stderr)
    sys.exit(2)

def parse_rings(text: str):
    vals = []
    for p in (text or "").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            vals.append(int(p))
        except ValueError:
            pass
    return vals

def find_build_matrix(yaml_doc):
    jobs = yaml_doc.get("jobs", {})
    build = jobs.get("build", {})
    strat = build.get("strategy", {})
    matrix = strat.get("matrix", {})
    return matrix.get("city", [])

def main():
    wf_path = pathlib.Path(os.environ.get("GOODBIRDS_WORKFLOW_YAML", ".github/workflows/build.yml"))
    base_url = os.environ.get("GOODBIRDS_BASE_URL", "https://goodbirds.org").rstrip("/")

    if not wf_path.exists():
        print(f"Workflow YAML not found at {wf_path}", file=sys.stderr)
        sys.exit(1)

    with wf_path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    cities = []
    for c in find_build_matrix(doc):
        slug = c.get("slug", "").strip()
        title = c.get("title", "").strip()
        lat = float(c.get("center_lat"))
        lon = float(c.get("center_lon"))
        rings = parse_rings(c.get("ring_kms", ""))
        max_radius = max(rings) if rings else int(c.get("default_radius_km", "20"))
        latest_href = c.get("latest_href") or f"maps/{c.get('maps_subdir','')}/latest.html"
        latest_url = f"{base_url}/{latest_href.lstrip('/')}"
        cities.append({
            "slug": slug,
            "title": title,
            "lat": lat,
            "lon": lon,
            "maxRadiusKm": max_radius,
            "latestUrl": latest_url,
        })

    out = pathlib.Path("docs/cities.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cities, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} with {len(cities)} cities")

if __name__ == "__main__":
    main()

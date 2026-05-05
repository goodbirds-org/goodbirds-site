#!/usr/bin/env python3
import html
import json
import os
import pathlib
import re
import sys

try:
    import yaml  # PyYAML
except ImportError:
    print("PyYAML not installed. pip install pyyaml", file=sys.stderr)
    sys.exit(2)

STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


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


def clean_location_name(title: str) -> str:
    value = str(title or "").strip()
    value = re.sub(r"\s*&\s*Vicinity$", "", value, flags=re.I)
    value = re.sub(r"\s+and\s+Vicinity$", "", value, flags=re.I)
    parts = [part.strip() for part in value.split(",")]
    if len(parts) >= 2:
        region = parts[-1]
        if region in STATE_NAMES or region == "QC":
            return ", ".join(parts[:-1]).strip()
    return value


def location_group(title: str) -> str:
    value = str(title or "").strip()
    value = re.sub(r"\s*&\s*Vicinity$", "", value, flags=re.I)
    value = re.sub(r"\s+and\s+Vicinity$", "", value, flags=re.I)
    parts = [part.strip() for part in value.split(",")]
    if len(parts) >= 2:
        region = parts[-1]
        if region in STATE_NAMES:
            return STATE_NAMES[region]
    return "Non-US"


def write_cities_map(cities, out_path: pathlib.Path):
    # Keep the payload embedded so docs/cities_map.html also works as a standalone page.
    cities_json = json.dumps(cities, ensure_ascii=False)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Goodbirds Coverage Map</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIINfQnk5D1w8f7GN8VJYUNXFaZ51yR3G4=" crossorigin="">
  <style>
    html, body, #map {{
      height:100%;
      margin:0;
      padding:0;
    }}

    body {{
      font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
      color:#111;
    }}

    .leaflet-tooltip.goodbirds-tooltip {{
      background:#fff;
      border:1px solid #d8d8d8;
      border-radius:10px;
      box-shadow:0 2px 8px rgba(0,0,0,.16);
      color:#111;
      padding:8px 10px;
    }}

    .goodbirds-tooltip a,
    .goodbirds-popup a {{
      color:#1f6fa8;
      font-weight:650;
      text-decoration:none;
    }}

    .goodbirds-tooltip a:hover,
    .goodbirds-popup a:hover {{
      text-decoration:underline;
    }}

    .goodbirds-popup-title {{
      font-weight:700;
      font-size:15px;
      margin-bottom:4px;
    }}

    .goodbirds-popup-meta {{
      color:#555;
      font-size:13px;
      margin-bottom:8px;
    }}

    .map-title {{
      position:absolute;
      top:12px;
      left:50%;
      transform:translateX(-50%);
      z-index:500;
      background:rgba(255,255,255,.94);
      border:1px solid #ddd;
      border-radius:999px;
      box-shadow:0 1px 4px rgba(0,0,0,.12);
      padding:7px 13px;
      font-weight:700;
      pointer-events:none;
      white-space:nowrap;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="map-title">Goodbirds Locations</div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const cities = {cities_json};

    function escapeHtml(value) {{
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }}

    function markerHtml() {{
      return '<div style="width:16px;height:16px;border-radius:50%;background:#2c7fb8;border:2px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.35);"></div>';
    }}

    const map = L.map("map", {{ scrollWheelZoom:false }});

    L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom:19,
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }}).addTo(map);

    const icon = L.divIcon({{
      className:"goodbirds-location-marker",
      html:markerHtml(),
      iconSize:[20,20],
      iconAnchor:[10,10],
      popupAnchor:[0,-10]
    }});

    const bounds = [];

    cities.forEach(city => {{
      if (typeof city.lat !== "number" || typeof city.lon !== "number") return;

      const label = escapeHtml(city.displayName || city.title || city.slug);
      const group = escapeHtml(city.group || "");
      const url = escapeHtml(city.latestUrl || "#");
      const radius = escapeHtml(city.maxRadiusKm || "");

      const tooltip = `
        <div>
          <strong>${{label}}</strong><br>
          <a href="${{url}}" target="_top" rel="noopener">Open location map</a>
        </div>`;

      const popup = `
        <div class="goodbirds-popup">
          <div class="goodbirds-popup-title">${{label}}</div>
          <div class="goodbirds-popup-meta">${{group}}${{radius ? " · " + radius + " km max radius" : ""}}</div>
          <a href="${{url}}" target="_top" rel="noopener">Open location map</a>
        </div>`;

      const marker = L.marker([city.lat, city.lon], {{ icon, title: label }})
        .bindTooltip(tooltip, {{
          className:"goodbirds-tooltip",
          direction:"top",
          offset:[0,-10],
          opacity:1,
          sticky:true,
          interactive:true
        }})
        .bindPopup(popup)
        .addTo(map);

      marker.on("mouseover", function() {{
        marker.openTooltip();
      }});

      bounds.push([city.lat, city.lon]);
    }});

    if (bounds.length) {{
      map.fitBounds(bounds, {{ padding:[28,28] }});
    }} else {{
      map.setView([39.5,-98.35], 4);
    }}
  </script>
</body>
</html>
"""
    out_path.write_text(html_text, encoding="utf-8")


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
            "displayName": clean_location_name(title),
            "group": location_group(title),
            "lat": lat,
            "lon": lon,
            "maxRadiusKm": max_radius,
            "latestUrl": latest_url,
        })

    docs_dir = pathlib.Path("docs")
    docs_dir.mkdir(parents=True, exist_ok=True)

    json_out = docs_dir / "cities.json"
    json_out.write_text(json.dumps(cities, indent=2) + "\n", encoding="utf-8")

    map_out = docs_dir / "cities_map.html"
    write_cities_map(cities, map_out)

    print(f"Wrote {json_out} with {len(cities)} cities")
    print(f"Wrote {map_out}")


if __name__ == "__main__":
    main()

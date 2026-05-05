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


def _strip_vicinity(title: str) -> str:
    value = str(title or "").strip()
    value = re.sub(r"\s*&\s*Vicinity$", "", value, flags=re.I)
    value = re.sub(r"\s+and\s+Vicinity$", "", value, flags=re.I)
    return value.strip()


def clean_location_name(title: str) -> str:
    value = _strip_vicinity(title)
    parts = [part.strip() for part in value.split(",")]
    if len(parts) >= 2:
        region = parts[-1]
        if region in STATE_NAMES or region == "QC":
            return ", ".join(parts[:-1]).strip()
    return value


def location_group(title: str) -> str:
    value = _strip_vicinity(title)
    parts = [part.strip() for part in value.split(",")]
    if len(parts) >= 2:
        region = parts[-1]
        if region in STATE_NAMES:
            return STATE_NAMES[region]
    return "Non-US"


def write_cities_map(cities, out_path: pathlib.Path):
    """Write a simple Leaflet coverage map.

    This intentionally uses Leaflet circleMarker instead of custom divIcon markers. Circle markers
    do not depend on image assets or CSS-generated marker icons, so they are less fragile in a
    GitHub Pages iframe. The map starts on the main US/Canada coverage area so the location markers
    are visible immediately; a Fit all button includes Aruba and any future non-US locations.
    """
    cities_json = json.dumps(cities, ensure_ascii=False)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Goodbirds Coverage Map</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{
      height:100%;
      margin:0;
      padding:0;
    }}

    body {{
      font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
      color:#111;
      background:#f7f7f7;
    }}

    #map {{
      position:absolute;
      inset:0;
      min-height:360px;
      background:#eef3f6;
    }}

    .map-title {{
      position:absolute;
      top:12px;
      left:50%;
      transform:translateX(-50%);
      z-index:500;
      background:rgba(255,255,255,.95);
      border:1px solid #ddd;
      border-radius:999px;
      box-shadow:0 1px 4px rgba(0,0,0,.12);
      padding:7px 13px;
      font-weight:700;
      pointer-events:none;
      white-space:nowrap;
    }}

    .map-button {{
      position:absolute;
      right:12px;
      top:12px;
      z-index:510;
      background:#fff;
      border:1px solid #ccc;
      border-radius:999px;
      box-shadow:0 1px 4px rgba(0,0,0,.12);
      color:#111;
      cursor:pointer;
      font:600 13px/1 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
      padding:8px 11px;
    }}

    .map-button:hover {{
      background:#f3f7fa;
    }}

    .leaflet-tooltip.goodbirds-tooltip {{
      background:#fff;
      border:1px solid #d8d8d8;
      border-radius:10px;
      box-shadow:0 2px 8px rgba(0,0,0,.16);
      color:#111;
      padding:8px 10px;
    }}

    .goodbirds-tooltip-title,
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
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="map-title">Goodbirds Locations</div>
  <button class="map-button" type="button" id="fit-all">Fit all</button>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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

    function validCity(city) {{
      return Number.isFinite(city.lat) && Number.isFinite(city.lon);
    }}

    const map = L.map("map", {{
      scrollWheelZoom:false,
      preferCanvas:true,
      worldCopyJump:true
    }}).setView([39.5, -98.35], 4);

    L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom:18,
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }}).addTo(map);

    const allBounds = [];
    const northAmericaBounds = [];

    cities.filter(validCity).forEach(city => {{
      const label = escapeHtml(city.displayName || city.title || city.slug || "Location");
      const group = escapeHtml(city.group || "");
      const url = escapeHtml(city.latestUrl || "#");
      const radius = city.maxRadiusKm ? escapeHtml(city.maxRadiusKm) : "";
      const latlng = [city.lat, city.lon];

      const tooltip = `
        <div class="goodbirds-tooltip-inner">
          <div class="goodbirds-tooltip-title">${{label}}</div>
          <a href="${{url}}" target="_top" rel="noopener">Open location map</a>
        </div>`;

      const popup = `
        <div class="goodbirds-popup">
          <div class="goodbirds-popup-title">${{label}}</div>
          <div class="goodbirds-popup-meta">${{group}}${{radius ? " · " + radius + " km max radius" : ""}}</div>
          <a href="${{url}}" target="_top" rel="noopener">Open location map</a>
        </div>`;

      const marker = L.circleMarker(latlng, {{
        radius:8,
        color:"#ffffff",
        weight:2,
        fillColor:"#2c7fb8",
        fillOpacity:0.95,
        opacity:1
      }}).addTo(map);

      marker.bindTooltip(tooltip, {{
        className:"goodbirds-tooltip",
        direction:"top",
        offset:[0,-8],
        opacity:1,
        sticky:true,
        interactive:true
      }});

      marker.bindPopup(popup, {{ maxWidth:260 }});
      marker.on("mouseover", function() {{ marker.openTooltip(); }});

      allBounds.push(latlng);

      // Keep the initial view focused on the primary US/Canada coverage area.
      // Non-US locations such as Aruba are still present and included by the Fit all button.
      if (city.lon > -170 && city.lon < -50 && city.lat > 20 && city.lat < 70) {{
        northAmericaBounds.push(latlng);
      }}
    }});

    function fitBounds(points) {{
      if (!points.length) return;
      map.fitBounds(points, {{ padding:[34,34], maxZoom:5 }});
    }}

    fitBounds(northAmericaBounds.length ? northAmericaBounds : allBounds);

    document.getElementById("fit-all").addEventListener("click", function() {{
      fitBounds(allBounds);
    }});
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

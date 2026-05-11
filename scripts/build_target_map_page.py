#!/usr/bin/env python3
"""Build a Folium target-species map page from docs/targets/<slug>/data/observations.json."""
import argparse
import json
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import folium
from folium.plugins import Fullscreen, LocateControl, MousePosition

GA_SNIPPET = """
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-NYEBPC2JEZ"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-NYEBPC2JEZ');
</script>
"""

VERSION = "GOODBIRDS_TARGET_SPECIES_V7_FOLIUM_RENDER_FIX_2026-05-11"


def esc(s):
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#039;"))


def fmt_updated(value):
    if not value:
        return "unknown update time"
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p %Z")
    except Exception:
        return str(value)


def loc_key(obs):
    if obs.get("locId"):
        return str(obs.get("locId"))
    try:
        return f"{float(obs.get('lat')):.5f},{float(obs.get('lng')):.5f}"
    except Exception:
        return obs.get("locName") or "unknown"


def count_birds(obs_list):
    total = 0
    for obs in obs_list:
        try:
            val = int(float(obs.get("howMany")))
        except Exception:
            val = 1
        total += max(1, val)
    return total


def species_match(obs, species):
    vals = {
        str(obs.get("speciesCode") or "").strip().lower(),
        str(obs.get("code") or "").strip().lower(),
        str(obs.get("displayName") or "").strip().lower(),
        str(obs.get("comName") or "").strip().lower(),
    }
    return str(species.get("code") or "").strip().lower() in vals or str(species.get("name") or "").strip().lower() in vals


def make_popup(obs):
    name = esc(obs.get("displayName") or obs.get("comName") or "Target species")
    loc = esc(obs.get("locName") or "Unknown location")
    dt = esc(obs.get("obsDt") or "")
    count = obs.get("howMany")
    count_text = ""
    try:
        c = int(float(count))
        count_text = f", {c} bird" + ("" if c == 1 else "s")
    except Exception:
        pass
    cid = obs.get("subId") or ""
    checklist = f"<div><a href='https://ebird.org/checklist/{esc(cid)}' target='_blank' rel='noopener'>Open eBird checklist</a></div>" if cid else ""
    return folium.Popup(
        f"<div style='font-size:13px;line-height:1.35'>"
        f"<div style='font-weight:700;margin-bottom:4px'>{name}</div>"
        f"<div>{loc}</div>"
        f"<div>{dt}{count_text}</div>"
        f"{checklist}</div>",
        max_width=340,
    )


def icon_html(color):
    color = esc(color or "#666666")
    return (
        f"<div style='width:14px;height:14px;border-radius:50%;"
        f"background:{color};border:1.5px solid #111827;box-shadow:0 1px 3px rgba(0,0,0,.35);'></div>"
    )


def add_rings(m, center, dist_km):
    try:
        dist = float(dist_km)
    except Exception:
        dist = 0
    for mi in [1, 5, 10, 20]:
        if mi * 1.609344 <= dist + 0.1:
            folium.Circle(
                location=center,
                radius=mi * 1609.344,
                color="#64748b",
                weight=1,
                opacity=0.25,
                fill=False,
                interactive=False,
            ).add_to(m)


def build_legend(title, updated, back_days, species_rows, total_locations, layer_names):
    rows_html = []
    for row in species_rows:
        code = esc(row["code"])
        name = esc(row["name"])
        color = esc(row["color"])
        bird_count = int(row["bird_count"])
        sighting_count = int(row["sighting_count"])
        rows_html.append(f"""
          <label class="gb-species-row" data-species="{code}">
            <input type="checkbox" checked data-layer="{code}" aria-label="Show {name}">
            <span class="gb-swatch" style="background:{color}"></span>
            <span><span class="gb-species-name">{name}</span><span class="gb-counts">{bird_count} bird{'s' if bird_count != 1 else ''}, {sighting_count} sighting{'s' if sighting_count != 1 else ''}</span></span>
          </label>
        """)
    layer_json = json.dumps(layer_names)
    total_text = f"{total_locations} location{'s' if total_locations != 1 else ''} with sightings"
    return f"""
    <style>
      .gb-panel {{ position: fixed; z-index: 9999; left: 16px; top: 16px; width: min(300px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; background: rgba(255,255,255,.95); border-radius: 12px; box-shadow: 0 10px 35px rgba(0,0,0,.22); padding: 11px; box-sizing: border-box; font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#1f2933; }}
      .gb-panel h1 {{ margin: 0 0 4px; font-size: 16px; line-height: 1.2; }}
      .gb-meta {{ margin: 0 0 10px; color: #52616b; font-size: 10.5px; line-height: 1.35; }}
      .gb-total {{ font-weight: 700; margin: 8px 0 10px; font-size: 12px; }}
      .gb-species-list {{ display: grid; gap: 5px; margin: 0 0 9px; }}
      .gb-species-row {{ display: grid; grid-template-columns: 17px 13px 1fr; gap: 6px; align-items: start; padding: 5px; border: 1px solid #e3e8ef; border-radius: 8px; background: #fff; }}
      .gb-species-row input {{ margin: 2px 0 0 0; }}
      .gb-swatch {{ width: 10px; height: 10px; border-radius: 50%; margin-top: 3px; border: 1px solid rgba(0,0,0,.35); }}
      .gb-species-name {{ font-weight: 700; font-size: 12px; }}
      .gb-counts {{ display: block; margin-top: 1px; color: #52616b; font-size: 10.5px; }}
      .gb-row-off {{ opacity: .48; }}
      .gb-footer {{ border-top: 1px solid #e3e8ef; padding-top: 8px; font-size: 10.5px; }}
      .gb-footer a {{ color: #0f766e; font-weight: 700; text-decoration: none; }}
      .gb-footer a:hover {{ text-decoration: underline; }}
      .leaflet-control-layers {{ display: none; }}
      @media (max-width: 640px) {{ .gb-panel {{ left: 10px; right: 10px; top: 10px; width: auto; max-height: 44vh; }} }}
    </style>
    <aside class="gb-panel" aria-label="Target species legend">
      <h1>{esc(title)}</h1>
      <p class="gb-meta">Updated {esc(updated)}. Showing sightings from the last {esc(back_days)} days.</p>
      <div class="gb-total" id="location-total">{esc(total_text)}</div>
      <div class="gb-species-list">{''.join(rows_html)}</div>
      <div class="gb-footer"><a href="../">All target species maps</a></div>
    </aside>
    <script>
      (function() {{
        var layerNames = {layer_json};
        function getLayer(code) {{
          var name = layerNames[code];
          if (!name) return null;
          return window[name] || null;
        }}
        function updateTotal() {{
          var total = 0;
          document.querySelectorAll('.gb-species-row input[type="checkbox"]').forEach(function(cb) {{
            if (cb.checked) total += Number(cb.getAttribute('data-locations') || 0);
          }});
        }}
        document.querySelectorAll('.gb-species-row input[type="checkbox"]').forEach(function(cb) {{
          cb.addEventListener('change', function() {{
            var row = cb.closest('.gb-species-row');
            var layer = getLayer(cb.getAttribute('data-layer'));
            if (!layer || !window.MAP_NAME_PLACEHOLDER) return;
            if (cb.checked) {{ layer.addTo(window.MAP_NAME_PLACEHOLDER); row.classList.remove('gb-row-off'); }}
            else {{ window.MAP_NAME_PLACEHOLDER.removeLayer(layer); row.classList.add('gb-row-off'); }}
          }});
        }});
      }})();
    </script>
    """


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Path to observations.json")
    ap.add_argument("--out", required=True, help="Output HTML path")
    ap.add_argument("--title", required=True, help="Map title")
    ap.add_argument("--zoom", default="10")
    args = ap.parse_args()

    data_path = Path(args.data)
    out_path = Path(args.out)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    species = data.get("species") or []
    observations = data.get("observations") or []
    center = [float(data.get("centerLat") or 0), float(data.get("centerLng") or 0)]

    m = folium.Map(location=center, zoom_start=int(args.zoom), control_scale=True)
    Fullscreen().add_to(m)
    LocateControl(auto_start=False, keepCurrentZoomLevel=False).add_to(m)
    MousePosition(separator=" , ", prefix="Lat, Lon:").add_to(m)
    add_rings(m, center, data.get("distKm") or 0)

    species_rows = []
    layer_names = OrderedDict()
    bounds = []
    total_locations = set()

    for sp in species:
        sp_obs = [o for o in observations if species_match(o, sp)]
        layer = folium.FeatureGroup(name=sp.get("name") or sp.get("code") or "Target species", show=True)
        layer.add_to(m)
        layer_names[str(sp.get("code") or sp.get("name"))] = layer.get_name()
        species_locations = set()
        for obs in sp_obs:
            try:
                lat = float(obs.get("lat")); lng = float(obs.get("lng"))
            except Exception:
                continue
            species_locations.add(loc_key(obs))
            total_locations.add(loc_key(obs))
            bounds.append([lat, lng])
            folium.Marker(
                location=[lat, lng],
                tooltip=obs.get("displayName") or obs.get("comName") or sp.get("name"),
                popup=make_popup(obs),
                icon=folium.DivIcon(
                    html=icon_html(obs.get("markerColor") or sp.get("color") or "#666666"),
                    icon_size=(14, 14),
                    icon_anchor=(7, 7),
                ),
            ).add_to(layer)
        species_rows.append({
            "code": str(sp.get("code") or sp.get("name")),
            "name": sp.get("name") or sp.get("code") or "Target species",
            "color": sp.get("color") or "#666666",
            "bird_count": count_birds(sp_obs),
            "sighting_count": len(sp_obs),
            "location_count": len(species_locations),
        })

    if bounds:
        m.fit_bounds(bounds, padding=(45, 45), max_zoom=12)

    folium.LayerControl(collapsed=True).add_to(m)
    map_name = m.get_name()
    legend = build_legend(args.title, fmt_updated(data.get("lastUpdated")), data.get("backDays") or "", species_rows, len(total_locations), layer_names)
    legend = legend.replace("MAP_NAME_PLACEHOLDER", map_name)
    m.get_root().html.add_child(folium.Element(legend))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))

    html = out_path.read_text(encoding="utf-8")
    html = html.replace("<head>", f"<head>\n  <!-- {VERSION} -->", 1)
    title_tag = f"<title>{esc(args.title)} | Goodbirds</title>"
    if "<title>" not in html:
        html = html.replace("</head>", f"  {title_tag}\n</head>", 1)
    html = html.replace("</body>", GA_SNIPPET + "\n</body>", 1)
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} with {len(observations)} observations")


if __name__ == "__main__":
    main()

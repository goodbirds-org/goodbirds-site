#!/usr/bin/env python3
"""Build a Folium target-species map page from docs/targets/<slug>/data/observations.json."""
import argparse
import json
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

VERSION = "GOODBIRDS_TARGET_SPECIES_V11_LOCATION_CHECKLISTS_2026-10-26"

GROUP_LABELS = OrderedDict([
    ("dabbler", "Dabbling ducks"),
    ("diver", "Diving ducks"),
    ("merganser", "Mergansers"),
    ("other", "Other waterbirds"),
])


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


def unique_sightings(observations):
    """Keep each retrieved species/checklist at a location once, without losing dates."""
    seen = set()
    result = []
    for obs in observations:
        species = obs.get("speciesCode") or obs.get("displayName") or obs.get("comName")
        record = obs.get("subId") or (obs.get("obsDt"), str(obs.get("howMany")))
        key = (loc_key(obs), species, record)
        if key not in seen:
            seen.add(key)
            result.append(obs)
    return result


def make_popup(observations, species):
    """Show every retrieved checklist, grouped by species, for one location."""
    loc = esc(observations[0].get("locName") or "Unknown location")
    sections = []
    for sp in species:
        sightings = unique_sightings([o for o in observations if species_match(o, sp)])
        if not sightings:
            continue
        entries = []
        for obs in sorted(sightings, key=lambda o: str(o.get("obsDt") or ""), reverse=True):
            count = obs.get("howMany")
            count_text = f" · {esc(count)} reported" if count is not None else " · count not reported"
            cid = obs.get("subId")
            link = (f"<a href='https://ebird.org/checklist/{esc(cid)}' target='_blank' rel='noopener'>"
                    f"Checklist {esc(cid)}</a>" if cid else "Checklist link unavailable")
            entries.append(f"<li>{esc(obs.get('obsDt') or 'Date unavailable')}{count_text}<br>{link}</li>")
        sections.append(f"<section><strong>{esc(sp.get('name') or sp.get('code'))}</strong>"
                        f"<ul style='padding-left:18px;margin:4px 0 12px'>{''.join(entries)}</ul></section>")
    return folium.Popup(
        f"<div style='font-size:13px;line-height:1.4;max-height:300px;overflow-y:auto'>"
        f"<strong>{loc}</strong><p style='margin:4px 0 10px'>"
        f"{len(sections)} species · all checklists associated with retrieved sightings</p>"
        f"{''.join(sections)}</div>",
        max_width=380,
    )


def icon_html(color, group=None, size=24):
    color = esc(color or "#666666")
    if group in GROUP_LABELS:
        # Shared duck silhouette, with waves, a dive arrow or a long bill.
        body = '<path d="M3 13 Q8 10 12 12 L14 8 Q14 4 18 4 Q22 4 22 8 L25 9 L21 11 Q22 19 13 20 Q5 21 3 13Z"/>'
        detail = {
            "dabbler": '<path d="M3 24 Q6 22 9 24 T15 24 T21 24" fill="none"/>',
            "diver": '<path d="M27 17 V27 M24 24 L27 27 L30 24" fill="none"/>',
            "merganser": '<path d="M21 8 L30 9 L21 11Z"/>',
            "other": '<circle cx="16" cy="15" r="10"/><circle cx="16" cy="15" r="4" fill="white" stroke="none"/>',
        }[group]
        silhouette = "" if group == "other" else body
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                f'viewBox="0 0 32 32" role="img" aria-label="{GROUP_LABELS[group]}" '
                f'style="overflow:visible;filter:drop-shadow(0 1px 1px white)">'
                f'<g fill="{color}" stroke="#111827" stroke-width="1.5" stroke-linejoin="round">'
                f'{silhouette}{detail}</g></svg>')
    return (
        f"<div style='width:14px;height:14px;border-radius:50%;"
        f"background:{color};border:1.5px solid #111827;box-shadow:0 1px 3px rgba(0,0,0,.35);'></div>"
    )


def ring_config_for_target(title, out_path):
    """Return custom map ring specs. Rings display on the map only, not in the legend."""
    key = f"{title} {out_path}".lower()
    if "aruba" in key or "noord" in key:
        return {"unit": "km", "values": [1, 5, 10]}
    if "estero" in key:
        return {"unit": "mi", "values": [1, 5, 10, 20]}
    return {"unit": "mi", "values": [1, 5, 10, 20]}


def add_rings(m, center, ring_config):
    """Add custom visible radius rings to the map only, not the legend."""
    unit = (ring_config or {}).get("unit", "mi")
    values = list((ring_config or {}).get("values") or [])

    folium.CircleMarker(
        location=center,
        radius=4,
        color="#2c7fb8",
        fill=True,
        fill_opacity=1,
        tooltip="Search center",
        interactive=False,
    ).add_to(m)

    if not values:
        return

    for value in values:
        try:
            ring_value = float(value)
        except Exception:
            continue

        radius_m = ring_value * 1000.0 if unit == "km" else ring_value * 1609.344
        label = f"{int(ring_value) if ring_value.is_integer() else ring_value:g} {unit}"
        is_outer = value == values[-1]
        color = "#08519c" if is_outer else "#475569"

        folium.Circle(
            location=center,
            radius=radius_m,
            color=color,
            weight=2 if is_outer else 1.5,
            opacity=0.75 if is_outer else 0.55,
            fill=False,
            dash_array=None if is_outer else "6,8",
            interactive=False,
        ).add_to(m)

        label_lat = center[0] + ((radius_m / 1000.0) / 111.0)
        label_html = (
            f"<div style='font:600 11px/1.1 system-ui,-apple-system,Segoe UI,sans-serif;"
            f"color:{color};background:rgba(255,255,255,.82);border:1px solid rgba(71,85,105,.35);"
            f"border-radius:999px;padding:2px 5px;white-space:nowrap'>{label}</div>"
        )
        folium.Marker(
            location=[label_lat, center[1]],
            icon=folium.DivIcon(html=label_html, icon_size=(48, 16), icon_anchor=(24, 8)),
            interactive=False,
        ).add_to(m)


def build_legend(title, updated, back_days, species_rows, total_locations, layer_names, species_locations, note="", pending=False):
    rows_html = []
    for row in species_rows:
        code = esc(row["code"])
        name = esc(row["name"])
        color = esc(row["color"])
        bird_count = int(row["bird_count"])
        sighting_count = int(row["sighting_count"])
        swatch = (icon_html(row["color"], row.get("group"), size=18) if row.get("group") in GROUP_LABELS
                  else f'<span class="gb-swatch" style="background:{color}"></span>')
        rows_html.append(f"""
          <label class="gb-species-row" data-species="{code}">
            <input type="checkbox" checked data-layer="{code}" aria-label="Show {name}">
            {swatch}
            <span><span class="gb-species-name">{name}</span><span class="gb-counts">{bird_count} bird{'s' if bird_count != 1 else ''}, {sighting_count} sighting{'s' if sighting_count != 1 else ''}</span></span>
          </label>
        """)
    layer_json = json.dumps(layer_names)
    species_locations_json = json.dumps(species_locations)
    total_text = f"{total_locations} location{'s' if total_locations != 1 else ''} with sightings"
    if pending:
        total_text = "Awaiting the first eBird update; sightings have not been fetched yet."
    groups = {r.get("group") for r in species_rows}
    group_key = "".join(f'<span style="display:inline-flex;align-items:center;gap:4px;margin:2px 8px 2px 0">'
                        f'{icon_html("#52616b", group, size=20)} {label}</span>'
                        for group, label in GROUP_LABELS.items() if group in groups)
    note_html = f'<p class="gb-meta">{esc(note)}</p>' if note else ""
    group_html = f'<div class="gb-meta" aria-label="Marker icon key">{group_key}</div>' if group_key else ""
    return f"""
    <style>
      .gb-panel {{ position: fixed; z-index: 9999; left: 16px; top: 16px; width: min(300px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; background: rgba(255,255,255,.95); border-radius: 12px; box-shadow: 0 10px 35px rgba(0,0,0,.22); padding: 11px; box-sizing: border-box; font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#1f2933; }}
      .gb-panel h1 {{ margin: 0 0 4px; font-size: 16px; line-height: 1.2; }}
      .gb-meta {{ margin: 0 0 10px; color: #52616b; font-size: 10.5px; line-height: 1.35; }}
      .gb-total {{ font-weight: 700; margin: 8px 0 8px; font-size: 12px; }}
      .gb-actions {{ display: flex; gap: 6px; margin: 0 0 9px; }}
      .gb-clear-species {{ appearance: none; border: 1px solid #cbd5e1; border-radius: 999px; background: #fff; color: #1f2933; cursor: pointer; font: 700 11px/1 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding: 6px 9px; }}
      .gb-clear-species:hover {{ background: #f1f5f9; }}
      .gb-clear-species:focus {{ outline: 2px solid #2c7fb8; outline-offset: 2px; }}
      .gb-species-list {{ display: grid; gap: 5px; margin: 0 0 9px; }}
      .gb-species-row {{ display: grid; grid-template-columns: 17px 18px 1fr; gap: 6px; align-items: start; padding: 5px; border: 1px solid #e3e8ef; border-radius: 8px; background: #fff; }}
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
      {note_html}
      <p class="gb-meta">Updated {esc(updated)}. Showing sightings from the last {esc(back_days)} days.</p>
      {group_html}
      <div class="gb-total" id="location-total">{esc(total_text)}</div>
      <div class="gb-actions"><button type="button" class="gb-clear-species" id="gb-clear-species">Clear species</button></div>
      <div class="gb-species-list">{''.join(rows_html)}</div>
      <div class="gb-footer"><a href="../">All target species maps</a></div>
    </aside>
    <script>
      (function() {{
        var layerNames = {layer_json};
        var speciesLocations = {species_locations_json};
        function getLayer(code) {{
          var name = layerNames[code];
          if (!name) return null;
          return window[name] || null;
        }}
        function updateTotal() {{
          var locations = new Set();
          document.querySelectorAll('.gb-species-row input[type="checkbox"]').forEach(function(cb) {{
            if (!cb.checked) return;
            var code = cb.getAttribute('data-layer');
            (speciesLocations[code] || []).forEach(function(loc) {{ locations.add(loc); }});
          }});
          var count = locations.size;
          var label = count === 1 ? 'location' : 'locations';
          var totalEl = document.getElementById('location-total');
          if (totalEl) totalEl.textContent = count + ' ' + label + ' with sightings';
        }}
        function setSpeciesChecked(cb, checked) {{
          var row = cb.closest('.gb-species-row');
          var layer = getLayer(cb.getAttribute('data-layer'));
          if (!layer || !window.MAP_NAME_PLACEHOLDER) return;
          cb.checked = checked;
          if (checked) {{
            if (!window.MAP_NAME_PLACEHOLDER.hasLayer(layer)) layer.addTo(window.MAP_NAME_PLACEHOLDER);
            if (row) row.classList.remove('gb-row-off');
          }} else {{
            if (window.MAP_NAME_PLACEHOLDER.hasLayer(layer)) window.MAP_NAME_PLACEHOLDER.removeLayer(layer);
            if (row) row.classList.add('gb-row-off');
          }}
        }}
        document.querySelectorAll('.gb-species-row input[type="checkbox"]').forEach(function(cb) {{
          cb.addEventListener('change', function() {{
            setSpeciesChecked(cb, cb.checked);
            updateTotal();
          }});
        }});
        var clearButton = document.getElementById('gb-clear-species');
        if (clearButton) {{
          clearButton.addEventListener('click', function() {{
            document.querySelectorAll('.gb-species-row input[type="checkbox"]').forEach(function(cb) {{
              setSpeciesChecked(cb, false);
            }});
            updateTotal();
          }});
        }}
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
    observations = unique_sightings(data.get("observations") or [])
    location_observations = OrderedDict()
    for obs in observations:
        if any(species_match(obs, sp) for sp in species):
            location_observations.setdefault(loc_key(obs), []).append(obs)
    center = [float(data.get("centerLat") or 0), float(data.get("centerLng") or 0)]

    m = folium.Map(location=center, zoom_start=int(args.zoom), control_scale=True)
    Fullscreen().add_to(m)
    LocateControl(auto_start=False, keepCurrentZoomLevel=False).add_to(m)
    MousePosition(separator=" , ", prefix="Lat, Lon:").add_to(m)
    add_rings(m, center, data.get("ringConfig") or ring_config_for_target(args.title, out_path))

    species_rows = []
    layer_names = OrderedDict()
    species_locations = OrderedDict()
    bounds = []
    total_locations = set()

    for sp in species:
        sp_obs = [o for o in observations if species_match(o, sp)]
        layer = folium.FeatureGroup(name=sp.get("name") or sp.get("code") or "Target species", show=True)
        layer.add_to(m)
        layer_names[str(sp.get("code") or sp.get("name"))] = layer.get_name()
        species_locations_for_row = set()
        sp_locations = OrderedDict()
        for obs in sp_obs:
            sp_locations.setdefault(loc_key(obs), []).append(obs)
        for location, sightings in sp_locations.items():
            obs = sightings[0]
            try:
                lat = float(obs.get("lat")); lng = float(obs.get("lng"))
            except Exception:
                continue
            species_locations_for_row.add(loc_key(obs))
            total_locations.add(loc_key(obs))
            bounds.append([lat, lng])
            folium.Marker(
                location=[lat, lng],
                tooltip=obs.get("displayName") or obs.get("comName") or sp.get("name"),
                popup=make_popup(location_observations[location], species),
                icon=folium.DivIcon(
                    html=icon_html(obs.get("markerColor") or sp.get("color") or "#666666", sp.get("group")),
                    icon_size=(24, 24) if sp.get("group") in GROUP_LABELS else (14, 14),
                    icon_anchor=(12, 12) if sp.get("group") in GROUP_LABELS else (7, 7),
                ),
            ).add_to(layer)
        species_locations[str(sp.get("code") or sp.get("name"))] = sorted(species_locations_for_row)
        species_rows.append({
            "code": str(sp.get("code") or sp.get("name")),
            "name": sp.get("name") or sp.get("code") or "Target species",
            "color": sp.get("color") or "#666666",
            "group": sp.get("group"),
            "bird_count": count_birds(sp_obs),
            "sighting_count": len(sp_obs),
            "location_count": len(species_locations_for_row),
        })

    if bounds and not data.get("ringConfig"):
        m.fit_bounds(bounds, padding=(45, 45), max_zoom=12)

    folium.LayerControl(collapsed=True).add_to(m)
    map_name = m.get_name()
    legend = build_legend(args.title, fmt_updated(data.get("lastUpdated")), data.get("backDays") or "", species_rows, len(total_locations), layer_names, species_locations, data.get("note") or "", data.get("dataStatus") == "pending")
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

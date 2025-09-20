#!/usr/bin/env python3
"""
capture_and_post_bsky.py v2.1 (rotation)
- Daily rotation based on America/New_York date
- Each rotation item defines:
    name, map_url (canonical latest.html), latest_txt_url (to resolve actual page),
    post_text (caption + hashtags), alt_text
- Screenshots the resolved dated page, posts text with canonical latest.html
- Clickable link + hashtag facets via TextBuilder
- Optional DRY_RUN=1 to test without posting
- Optional ROTATION_OFFSET to shift which item is "today"
"""
import os
import sys
import pathlib
import tempfile
import re
from datetime import datetime, date
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright
from atproto import Client, client_utils

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

def log(msg: str):
    print(msg, file=sys.stderr)

# -------- rotation config --------
ROTATION = [
    {
        "name": "Manhattan",
        "map_url": "https://goodbirds.org/maps/manhattan/latest.html",
        "latest_txt_url": "https://goodbirds.org/maps/manhattan/latest.txt",
        "post_text": "Latest notable bird sightings in Manhattan 🗽🐦 #ebird #nyc #birds",
        "alt_text": "Full-page screenshot of the most recent Manhattan GoodBirds map",
    },
    {
        "name": "Cambridge",
        "map_url": "https://goodbirds.org/maps/cambridge/latest.html",
        "latest_txt_url": "https://goodbirds.org/maps/cambridge/latest.txt",
        "post_text": "Latest notable bird sightings in Cambridge 🐦📚 #ebird #cambridge #birds",
        "alt_text": "Full-page screenshot of the most recent Cambridge GoodBirds map",
    },
    {
        "name": "Chicago",
        "map_url": "https://goodbirds.org/maps/chicago/latest.html",
        "latest_txt_url": "https://goodbirds.org/maps/chicago/latest.txt",
        "post_text": "Latest notable bird sightings in Chicago 🌬️🐦 #ebird #chicago #birds",
        "alt_text": "Full-page screenshot of the most recent Chicago GoodBirds map",
    },
    {
        "name": "Portland, OR",
        "map_url": "https://goodbirds.org/maps/portland-or/latest.html",
        "latest_txt_url": "https://goodbirds.org/maps/portland-or/latest.txt",
        "post_text": "Latest notable bird sightings in Portland, OR 🌲🐦 #rosecity #pdx #ebird #birds",
        "alt_text": "Full-page screenshot of the most recent Portland, OR GoodBirds map",
    },
    {
        "name": "San Diego, CA",
        "map_url": "https://goodbirds.org/maps/san-diego/latest.html",
        "latest_txt_url": "https://goodbirds.org/maps/san-diego/latest.txt",
        "post_text": "Latest notable bird sightings in San Diego, CA 🌊🐦⚡ #ebird #sandiego #birds",
        "alt_text": "Full-page screenshot of the most recent San Diego GoodBirds map",
    },
]
# ---------------------------------

def fetch_text(url: str, timeout: int = 15) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")

def resolve_latest_map_url(map_url: str, latest_txt_url: str | None) -> str:
    if not latest_txt_url:
        return map_url
    try:
        txt = fetch_text(latest_txt_url).strip()
        chosen = None
        for line in txt.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            chosen = line
            break
        if not chosen:
            log(f"[warn] {latest_txt_url} had no usable lines. Falling back to MAP_URL.")
            return map_url
        base_dir = latest_txt_url.rsplit("/", 1)[0] + "/"
        return urljoin(base_dir, chosen)
    except Exception as e:
        log(f"[warn] Could not read {latest_txt_url}: {e}. Falling back to MAP_URL.")
        return map_url

def screenshot_page(target: str, out_path: str, viewport_width=1400, viewport_height=900, wait_ms=5000):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": viewport_width, "height": viewport_height})
        page = context.new_page()
        parsed = urlparse(target)
        if parsed.scheme in ("http", "https"):
            page.goto(target, wait_until="load", timeout=120_000)
        else:
            path = pathlib.Path(target).resolve()
            page.goto(f"file://{path}", wait_until="load", timeout=120_000)
        page.wait_for_timeout(wait_ms)
        page.screenshot(path=out_path, full_page=True, type="jpeg", quality=80)
        context.close()
        browser.close()
    return out_path

HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")

def build_text_and_facets(caption_text: str, canonical_url: str) -> tuple[str, list]:
    tb = client_utils.TextBuilder()
    idx = 0
    for m in HASHTAG_RE.finditer(caption_text):
        if m.start() > idx:
            tb.text(caption_text[idx:m.start()])
        tag_value = m.group(1)
        tb.tag("#" + tag_value, tag_value)
        idx = m.end()
    if idx < len(caption_text):
        tb.text(caption_text[idx:])
    tb.text("\n")
    tb.link(canonical_url, canonical_url)
    return tb.build_text(), tb.build_facets()

def pick_rotation_item(rotation: list, ny_date: date, offset: int = 0) -> dict:
    idx = (ny_date.toordinal() + offset) % len(rotation)
    return rotation[idx]

def pick_by_name(rotation: list, name: str) -> dict | None:
    target = name.strip().lower()
    for item in rotation:
        if item["name"].lower() == target:
            return item
    return None

def getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except Exception:
        return default

def main():
    log("[info] capture_and_post_bsky.py v2.1 starting")

    # Current date in New York for rotation
    if ZoneInfo:
        ny_now = datetime.now(ZoneInfo("America/New_York"))
    else:
        ny_now = datetime.utcnow()
    ny_date = ny_now.date()

    # Bluesky config
    bsky_handle = os.environ.get("BSKY_HANDLE", "").strip()
    bsky_app_password = os.environ.get("BSKY_APP_PASSWORD", "").strip()
    dry_run = os.environ.get("DRY_RUN", "0").strip() == "1"

    if not dry_run:
        problems = []
        if not bsky_handle:
            problems.append("BSKY_HANDLE is empty")
        if not bsky_app_password:
            problems.append("BSKY_APP_PASSWORD is empty")
        if bsky_handle.startswith("@"):
            problems.append("BSKY_HANDLE should not start with '@'")
        if problems:
            raise SystemExit(f"[fatal] Config error: {'; '.join(problems)}")
    else:
        log("[info] DRY_RUN=1. Will not contact Bluesky.")

    # Choose rotation item, with optional force by name
    force_city = os.environ.get("FORCE_CITY_NAME", "").strip()
    if force_city:
        item = pick_by_name(ROTATION, force_city)
        if not item:
            raise SystemExit(f"[fatal] FORCE_CITY_NAME '{force_city}' not found in ROTATION")
        log(f"[info] Force city selected: {item['name']}")
    else:
        rotation_offset = getenv_int("ROTATION_OFFSET", 0)
        item = pick_rotation_item(ROTATION, ny_date, rotation_offset)
        log(f"[info] Rotation item selected: {item['name']}")

    name = item["name"]
    map_url = item["map_url"]
    latest_txt_url = item.get("latest_txt_url")
    post_text = item["post_text"]
    alt_text = item["alt_text"]

    # Resolve the actual dated page to screenshot; keep canonical latest.html for the post text
    effective_url = resolve_latest_map_url(map_url, latest_txt_url)
    full_text, facets = build_text_and_facets(post_text, map_url)

    # Rendering and wait settings
    wait_ms = getenv_int("WAIT_MS", 5000)
    viewport_w = getenv_int("VIEWPORT_W", 1400)
    viewport_h = getenv_int("VIEWPORT_H", 900)

    log(f"[info] Today: {ny_date} New York | Posting: {name}")
    log(f"[info] Screenshot source: {effective_url}")

    with tempfile.TemporaryDirectory() as td:
        out_path = os.path.join(td, "map.jpg")
        screenshot_page(
            effective_url,
            out_path,
            viewport_width=viewport_w,
            viewport_height=viewport_h,
            wait_ms=wait_ms,
        )

        if dry_run:
            log("[info] DRY_RUN complete. Would post text:")
            log(full_text)
            log(f"[info] Facets: {facets}")
            log(f"[info] Screenshot saved at: {out_path}")
            return

        client = Client()
        did_info = client.com.atproto.identity.resolve_handle({"handle": bsky_handle})
        log(f"[info] Resolved handle {bsky_handle} -> {did_info.did}")
        client.login(bsky_handle, bsky_app_password)

        with open(out_path, "rb") as f:
            img_bytes = f.read()
        client.send_image(text=full_text, image=img_bytes, image_alt=alt_text, facets=facets)

    log("[info] Post complete")


if __name__ == "__main__":
    main()

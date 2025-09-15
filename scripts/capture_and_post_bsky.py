#!/usr/bin/env python3
"""
capture_and_post_bsky.py v1.5
- Resolves latest.txt to find the real map page for screenshot
- Posts the canonical MAP_URL (latest.html) in the text
- Uses TextBuilder to add a clickable link facet for MAP_URL
- Detects hashtags in POST_TEXT and adds tag facets
- Optional DRY_RUN=1 to skip Bluesky login when testing
"""
import os
import sys
import pathlib
import tempfile
import re
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright
from atproto import Client, models, client_utils


def log(msg: str):
    print(msg, file=sys.stderr)


def fetch_text(url: str, timeout: int = 15) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")


def derive_latest_txt_url(map_url: str) -> str | None:
    # .../latest.html -> .../latest.txt
    if map_url.lower().endswith(".html"):
        return re.sub(r"\.html$", ".txt", map_url, flags=re.IGNORECASE)
    return None


def resolve_latest_map_url(map_url: str, explicit_latest_txt: str | None = None) -> str:
    """
    Use latest.txt to resolve the actual dated map page.
    Falls back to MAP_URL if anything fails.
    """
    latest_txt_url = explicit_latest_txt or derive_latest_txt_url(map_url)
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
        resolved = urljoin(base_dir, chosen)
        return resolved
    except Exception as e:
        log(f"[warn] Could not read {latest_txt_url}: {e}. Falling back to MAP_URL.")
        return map_url


def screenshot_page(target: str, out_path: str, viewport_width=1400, viewport_height=900, wait_ms=5000):
    """
    Screenshot a URL or local HTML file to a JPEG.
    """
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

        # Give map tiles time to render
        page.wait_for_timeout(wait_ms)

        page.screenshot(path=out_path, full_page=True, type="jpeg", quality=80)

        context.close()
        browser.close()
    return out_path


HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")

def build_text_with_facets(post_text: str, map_url: str) -> tuple[str, list]:
    """
    Build the final post text and facets using TextBuilder:
    - Preserve your caption text, turning #tags into tag facets
    - Add a clickable link facet for map_url on a new line
    """
    tb = client_utils.TextBuilder()

    # Walk the caption and facet hashtags
    idx = 0
    for m in HASHTAG_RE.finditer(post_text):
        if m.start() > idx:
            tb.text(post_text[idx:m.start()])
        tb.tag(m.group(1))
        idx = m.end()
    if idx < len(post_text):
        tb.text(post_text[idx:])

    # New line, then link facet for the canonical latest.html
    tb.text("\n")
    tb.link(map_url, map_url)

    return tb.build_text(), tb.build_facets()


def post_to_bluesky(image_path: str, text: str, facets: list, handle: str, app_password: str, alt_text: str = "Map screenshot"):
    client = Client()

    # Resolve handle for a clearer error early
    did_info = client.com.atproto.identity.resolve_handle({"handle": handle})
    log(f"[info] Resolved handle {handle} -> {did_info.did}")

    client.login(handle, app_password)

    with open(image_path, "rb") as f:
        img_bytes = f.read()

    # Use high-level helper that supports facets with an attached image
    # Docs indicate facets can be passed with send_image and other helpers. :contentReference[oaicite:0]{index=0}
    client.send_image(text=text, image=img_bytes, image_alt=alt_text, facets=facets)


def getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except Exception:
        return default


def main():
    log("[info] capture_and_post_bsky.py v1.5 starting")

    map_url = os.environ.get("MAP_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not map_url:
        log("[fatal] Set MAP_URL or pass a URL or local HTML path as the first argument")
        sys.exit(2)

    latest_txt_url = os.environ.get("LATEST_TXT_URL") or None  # optional explicit
    bsky_handle = os.environ.get("BSKY_HANDLE", "").strip()
    bsky_app_password = os.environ.get("BSKY_APP_PASSWORD", "").strip()
    post_text = os.environ.get("POST_TEXT", "Latest map")
    alt_text = os.environ.get("ALT_TEXT", "Map screenshot")
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

    # Resolve the page to screenshot, but keep MAP_URL for posting
    effective_url = resolve_latest_map_url(map_url, latest_txt_url)

    # Build text and facets using TextBuilder - this guarantees a proper link facet. :contentReference[oaicite:1]{index=1}
    full_text, facets = build_text_with_facets(post_text, map_url)

    wait_ms = getenv_int("WAIT_MS", 5000)
    viewport_w = getenv_int("VIEWPORT_W", 1400)
    viewport_h = getenv_int("VIEWPORT_H", 900)

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

        post_to_bluesky(out_path, full_text, facets, bsky_handle, bsky_app_password, alt_text=alt_text)

    log("[info] Post complete")


if __name__ == "__main__":
    main()

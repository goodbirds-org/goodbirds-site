#!/usr/bin/env python3
import os
import sys
import pathlib
import tempfile
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright
from atproto import Client, models


def fetch_text(url: str, timeout: int = 15) -> str:
    """Fetch small text files like latest.txt with a normal browser UA."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")


def derive_latest_txt_url(map_url: str) -> str | None:
    """
    If MAP_URL looks like .../latest.html, try the sibling latest.txt.
    Otherwise return None.
    """
    if map_url.lower().endswith("latest.html"):
        return map_url[:-5] + "txt"  # replace .html -> .txt
    return None


def resolve_latest_map_url(map_url: str, explicit_latest_txt: str | None = None) -> str:
    """
    Return the final map URL to screenshot.
    Priority:
      1) explicit LATEST_TXT_URL if provided
      2) derived latest.txt next to latest.html
      3) fallback to MAP_URL itself
    """
    latest_txt_url = explicit_latest_txt or derive_latest_txt_url(map_url)
    if not latest_txt_url:
        return map_url

    try:
        txt = fetch_text(latest_txt_url).strip()
        # Use first non-empty, non-comment line
        chosen = None
        for line in txt.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            chosen = line
            break
        if not chosen:
            print(f"[warn] {latest_txt_url} had no usable lines. Falling back to MAP_URL.", file=sys.stderr)
            return map_url

        # If chosen is relative, join to directory of latest.txt
        base_dir = latest_txt_url.rsplit("/", 1)[0] + "/"
        resolved = urljoin(base_dir, chosen)
        return resolved
    except Exception as e:
        print(f"[warn] Could not read {latest_txt_url}: {e}. Falling back to MAP_URL.", file=sys.stderr)
        return map_url


def screenshot_page(target: str, out_path: str, viewport_width=1400, viewport_height=900, wait_ms=5000):
    """Screenshot a URL or local HTML file to a JPEG."""
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


def post_to_bluesky(image_path: str, text: str, handle: str, app_password: str, alt_text: str = "Map screenshot"):
    client = Client()
    client.login(handle, app_password)

    with open(image_path, "rb") as f:
        img_bytes = f.read()

    upload = client.upload_blob(img_bytes)
    images = [models.AppBskyEmbedImages.Image(alt=alt_text, image=upload.blob)]
    embed = models.AppBskyEmbedImages.Main(images=images)

    record = models.AppBskyFeedPost.Record(
        text=text,
        embed=embed,
        created_at=client.get_current_time_iso(),
    )
    client.app.bsky.feed.post.create(client.me.did, record)


def getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except Exception:
        return default


def main():
    map_url = os.environ.get("MAP_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not map_url:
        print("Set MAP_URL or pass a URL/local HTML path as the first argument", file=sys.stderr)
        sys.exit(2)

    latest_txt_url = os.environ.get("LATEST_TXT_URL") or None  # optional override

    bsky_handle = os.environ.get("BSKY_HANDLE")
    bsky_app_password = os.environ.get("BSKY_APP_PASSWORD")
    post_text = os.environ.get("POST_TEXT", "Latest map")
    alt_text = os.environ.get("ALT_TEXT", "Map screenshot")

    if not bsky_handle or not bsky_app_password:
        print("BSKY_HANDLE and BSKY_APP_PASSWORD env vars are required", file=sys.stderr)
        sys.exit(2)

    # Resolve the real map URL from latest.txt when available
    effective_url = resolve_latest_map_url(map_url, latest_txt_url)

    # Always append the link we are actually screenshotting
    full_text = f"{post_text}\n{effective_url}"

    wait_ms = getenv_int("WAIT_MS", 5000)
    viewport_w = getenv_int("VIEWPORT_W", 1400)
    viewport_h = getenv_int("VIEWPORT_H", 900)

    with tempfile.TemporaryDirectory() as td:
        out_path = os.path.join(td, "map.jpg")
        screenshot_page(effective_url, out_path, viewport_width=viewport_w, viewport_height=viewport_h, wait_ms=wait_ms)
        post_to_bluesky(out_path, full_text, bsky_handle, bsky_app_password, alt_text=alt_text)


if __name__ == "__main__":
    main()

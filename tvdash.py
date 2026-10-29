#!/usr/bin/env python3
"""Render dashboards to a 3840x2160 PNG, show them as art on a Samsung The Frame, take them away again, start YouTube."""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import tv

ROOT = Path(__file__).parent
DASHBOARD = ROOT / "dashboard" / "index.html"
MORNING = ROOT / "dashboard" / "morning.html"
OUT_DIR = Path(os.environ.get("TVDASH_OUT") or ROOT / "out")
WIDTH, HEIGHT = 3840, 2160


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding what is already set."""
    for line in path.expanduser().read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def fetch_camera(entity_id: str, out: Path, base_url: str) -> None:
    """Save the latest still of a Home Assistant camera entity (needs HASS_TOKEN)."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/camera_proxy/{entity_id}",
        headers={"Authorization": f"Bearer {os.environ['HASS_TOKEN']}"},
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(urllib.request.urlopen(req, timeout=20).read())


def render(url: str, out: Path, data: dict | None = None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # Locally the system Chrome is used; in the container TVDASH_BROWSER_CHANNEL="" selects Playwright's own Chromium.
        browser = p.chromium.launch(channel=os.environ.get("TVDASH_BROWSER_CHANNEL", "chrome") or None,
                                    args=["--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": WIDTH // 2, "height": HEIGHT // 2}, device_scale_factor=2)
        if data is not None:
            page.add_init_script(f"window.DATA = {json.dumps(data)};")
        page.goto(url, wait_until="networkidle")
        if url.startswith("file:"):
            page.wait_for_selector("body[data-ready='1']", timeout=15000)
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out))
        browser.close()


def make_morning(out: Path, day: date | None = None, print_data: bool = False) -> None:
    """The morning board for `day` (default today). Needs HASS_URL and HASS_TOKEN in the environment."""
    import morning

    data = morning.build(day)
    if print_data:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    render(MORNING.as_uri(), out, data)


def make_doorbell(out: Path, camera: str | None, hass_url: str | None) -> None:
    """The clock/weather dashboard, with the latest still of `camera` when given."""
    url = DASHBOARD.as_uri()
    if camera:
        if not hass_url:
            raise ValueError("a camera needs a Home Assistant URL (HASS_URL)")
        still = out.with_name(out.stem + "-camera.jpg")
        fetch_camera(camera, still, hass_url)
        url += "?" + urllib.parse.urlencode({"cam": still.as_uri()})
    render(url, out)


def push(host: str, image: Path) -> None:
    print(f"uploaded and selected {tv.show_image(host, image.read_bytes())} on {host}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="screenshot the dashboard to a PNG")
    r.add_argument("--url", default=DASHBOARD.as_uri(), help="page to screenshot (default: bundled dashboard; can be a Home Assistant URL)")
    r.add_argument("--out", type=Path, default=OUT_DIR / "dashboard.png")

    pu = sub.add_parser("push", help="upload a PNG to the TV and show it (remembers the art that was showing)")
    pu.add_argument("--host", required=True)
    pu.add_argument("--image", type=Path, default=OUT_DIR / "dashboard.png")

    a = sub.add_parser("all", help="render, then push")
    a.add_argument("--host", required=True)
    a.add_argument("--url", default=DASHBOARD.as_uri())

    m = sub.add_parser("morning", help="build the morning board from HA calendars + weather; push it when --host is given")
    m.add_argument("--host", help="TV to push to; without it the PNG is only rendered")
    m.add_argument("--date", type=date.fromisoformat, help="show this day instead of today (YYYY-MM-DD)")
    m.add_argument("--out", type=Path, default=OUT_DIR / "morning.png")
    m.add_argument("--config", type=Path, default=os.environ.get("TVDASH_CONFIG"), help="household settings (default: $TVDASH_CONFIG)")
    m.add_argument("--print-data", action="store_true", help="also print the collected data as JSON")

    for p in (r, a, m):
        p.add_argument("--hass-url", default=os.environ.get("HASS_URL"), help="Home Assistant base URL (default: $HASS_URL)")
        p.add_argument("--env-file", type=Path, help="file with HASS_URL / HASS_TOKEN lines")
    for p in (r, a):
        p.add_argument("--camera", help="HA camera entity to show on the bundled dashboard, e.g. camera.front_door")

    h = sub.add_parser("hide", help="remove our dashboard and put the previous art back")
    h.add_argument("--host", required=True)
    h.add_argument("--fallback", help="content id to show when no previous art was recorded (see `art`)")

    ls = sub.add_parser("art", help="list the art on the TV (to choose a --fallback)")
    ls.add_argument("--host", required=True)

    y = sub.add_parser("youtube", help="start a YouTube video on the TV (DIAL, no pairing needed)")
    y.add_argument("--host", required=True)
    y.add_argument("--video", required=True, help="video id or any YouTube URL")
    y.add_argument("--start", type=int, default=0, help="start offset in seconds")

    ys = sub.add_parser("youtube-stop", help="stop the YouTube app on the TV")
    ys.add_argument("--host", required=True)

    w = sub.add_parser("wake", help="wake the TV from standby (Wake-on-LAN) and wait until it answers")
    w.add_argument("--host", required=True)

    args = ap.parse_args()
    try:
        if args.cmd == "push":
            push(args.host, args.image)
        elif args.cmd == "hide":
            print(tv.hide(args.host, fallback=args.fallback))
        elif args.cmd == "art":
            for item in tv.list_art(args.host):
                print(item["content_id"], item["category"], item["date"], "(ours)" if item["ours"] else "")
        elif args.cmd == "youtube":
            print("started", tv.youtube_start(args.host, args.video, args.start))
        elif args.cmd == "wake":
            print("woken" if tv.wake(args.host) else "the TV was on already")
        elif args.cmd == "youtube-stop":
            print("stopped" if tv.youtube_stop(args.host) else "YouTube was not running")
        else:
            _render_commands(args)
    except (tv.TVError, ValueError) as error:   # ValueError includes morning.ConfigError
        sys.exit(str(error))


def _render_commands(args: argparse.Namespace) -> None:
    if args.env_file:
        load_env_file(args.env_file)
        args.hass_url = args.hass_url or os.environ.get("HASS_URL")

    if args.cmd == "morning":
        if not args.hass_url:
            sys.exit("morning needs --hass-url or HASS_URL")
        os.environ["HASS_URL"] = args.hass_url
        if not args.config:
            sys.exit("morning needs --config or TVDASH_CONFIG (see config.example.json)")
        os.environ["TVDASH_CONFIG"] = str(args.config)
        make_morning(args.out, args.date, args.print_data)
        print(f"wrote {args.out}")
        if args.host:
            push(args.host, args.out)
        return

    url = args.url
    if args.camera:
        if not args.hass_url:
            sys.exit("--camera needs --hass-url or HASS_URL")
        cam = OUT_DIR / "camera.jpg"
        fetch_camera(args.camera, cam, args.hass_url)
        url += "?" + urllib.parse.urlencode({"cam": cam.as_uri()})

    out = args.out if args.cmd == "render" else OUT_DIR / "dashboard.png"
    render(url, out)
    if args.cmd == "render":
        print(f"wrote {out}")
    else:
        push(args.host, out)


if __name__ == "__main__":
    main()

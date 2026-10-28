"""Everything that talks to the TV: show an image as art and put the previous art back, and start YouTube."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("TVDASH_STATE") or Path(__file__).parent / ".state")
REST_PORT, DIAL_PORT = 8001, 8080   # the TV's REST API (start/stop apps) and its DIAL service (gives the YouTube screen id)
YOUTUBE_APP_ID = "111299001912"


class TVError(RuntimeError):
    pass


# --- Art ------------------------------------------------------------------------------------------

def connect(host: str):
    """Open the art channel. The first connection needs an approval on the TV; the token is kept in STATE_DIR."""
    from samsungframetv import SamsungTVWS

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tv = SamsungTVWS(host=host, port=8002, token_file=str(STATE_DIR / f"token-{host}.txt"), timeout=30, name="tv-dashboard")
    art = tv.art()
    if not art.supported():
        raise TVError(f"{host}: art mode API not supported")
    return art


def _state_path(host: str) -> Path:
    return STATE_DIR / f"state-{host}.json"


def load_state(host: str) -> dict:
    """What we uploaded (we only ever delete our own uploads) and the art to go back to.

    `showing` is True from the moment a dashboard is up until `hide` has put the previous art back.
    """
    path = _state_path(host)
    if path.exists():
        return json.loads(path.read_text())
    legacy = STATE_DIR / f"uploads-{host}.json"   # written by earlier versions: just a list of content ids
    uploads = json.loads(legacy.read_text()) if legacy.exists() else []
    return {"uploads": uploads, "previous": None, "showing": bool(uploads)}


def save_state(host: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _state_path(host).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(_state_path(host))


def _slideshow_active(slideshow: dict | None) -> bool:
    return bool(slideshow) and slideshow.get("value") not in (None, "", "off")


def _capture(art, ours: list[str]) -> dict:
    """Remember what the TV shows now, to be able to go back to it."""
    current = (art.get_current() or {}).get("content_id")
    try:
        raw = art.get_slideshow_status() or {}
        slideshow = {k: raw.get(k) for k in ("value", "type", "category_id")}
    except Exception:   # some models do not answer the slideshow request at all
        slideshow = None
    return {
        "content_id": None if current in ours else current,   # never "go back" to one of our own images
        "artmode": art.get_artmode(),
        "slideshow": slideshow,
    }


def show_image(host: str, image: bytes, art=None) -> str:
    """Upload `image`, show it, and remember the art that was showing before (only for the first dashboard)."""
    art = art or connect(host)
    state = load_state(host)
    if not state["showing"]:
        state["previous"] = _capture(art, state["uploads"])
        state["showing"] = True
        save_state(host, state)   # saved before touching the TV, so a failure half way still can be undone
        if _slideshow_active(state["previous"]["slideshow"]):
            art.set_slideshow_status(0)   # otherwise the TV would rotate our image away again

    content_id = art.upload(image, matte="none", portrait_matte="none", file_type="png")
    art.select_image(content_id, show=True)
    old = [i for i in state["uploads"] if i != content_id]
    if old:
        art.delete_list(old)
    # set_artmode gets no reply (and times out) when the TV is already in art mode.
    if art.get_artmode() != "on":
        art.set_artmode(True)
    state["uploads"] = [content_id]
    save_state(host, state)
    return content_id


def hide(host: str, fallback: str | None = None, art=None) -> dict:
    """Remove our dashboard and put the art back that was showing before it.

    `fallback` is the content id to show when nothing was recorded (for example a dashboard that was put up
    by an older version of this tool). Without either, the TV simply picks its own art after our image is gone.
    """
    art = art or connect(host)
    state = load_state(host)
    previous = state["previous"] or {}
    target = previous.get("content_id") or fallback
    if target in state["uploads"]:
        target = fallback if fallback not in state["uploads"] else None

    if target:
        art.select_image(target, show=True)
    slideshow = previous.get("slideshow")
    if _slideshow_active(slideshow):
        category = int(str(slideshow.get("category_id") or "MY-C0002")[-1])
        art.set_slideshow_status(int(slideshow["value"]), slideshow.get("type") == "shuffleslideshow", category)
    if state["uploads"]:
        art.delete_list(state["uploads"])

    # Back to art mode (after YouTube the TV is in TV mode), unless it was in TV mode before the dashboard.
    wanted = previous.get("artmode") or "on"
    if art.get_artmode() != wanted:
        art.set_artmode(wanted == "on")

    save_state(host, {"uploads": [], "previous": None, "showing": False})
    return {"restored": target, "slideshow": bool(_slideshow_active(slideshow)), "artmode": wanted}


def list_art(host: str, art=None) -> list[dict]:
    """The art on the TV, to pick a `fallback` for `hide`."""
    art = art or connect(host)
    ours = set(load_state(host)["uploads"])
    seen, out = set(), []
    for item in art.available():
        cid = item["content_id"]
        if cid not in seen:
            seen.add(cid)
            out.append({"content_id": cid, "category": item.get("category_id"), "date": item.get("image_date"), "ours": cid in ours})
    return out


# --- YouTube ----------------------------------------------------------------------------------------
# What works on the Frame (tested): the app is started through the TV's REST API (also from art mode, no pairing),
# and the video is sent to YouTube's own cast service with the screen id that the app publishes through DIAL.
# What does not: starting a video with a DIAL POST (HTTP 403) and `run_app` over the WebSocket remote.

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}


def parse_youtube_id(video: str) -> str:
    """Accept a plain 11 character id or any common YouTube URL; return the id."""
    video = video.strip()
    if _YT_ID.match(video):
        return video
    url = urllib.parse.urlparse(video if "//" in video else "//" + video)
    host = (url.hostname or "").lower().removeprefix("www.")
    candidate = ""
    if host == "youtu.be":
        candidate = url.path.lstrip("/").split("/")[0]
    elif host in _YT_HOSTS:
        if url.path == "/watch":
            candidate = (urllib.parse.parse_qs(url.query).get("v") or [""])[0]
        else:
            match = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", url.path)
            candidate = match.group(1) if match else ""
    if not _YT_ID.match(candidate):
        raise ValueError(f"no YouTube video id found in {video!r}")
    return candidate


def _http(host: str, port: int, path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(f"http://{host}:{port}{path}", data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except OSError as error:
        raise TVError(f"{host}: not reachable on port {port} ({error})") from error


def _app(host: str, method: str = "GET") -> tuple[int, bytes]:
    return _http(host, REST_PORT, f"/api/v2/applications/{YOUTUBE_APP_ID}", method, b"" if method == "POST" else None)


def _app_status(host: str) -> dict:
    status, body = _app(host)
    if status != 200:
        raise TVError(f"{host}: app status request failed (HTTP {status})")
    return json.loads(body)


def youtube_state(host: str) -> str:
    """'running' or 'stopped' (an app that lingers in the background counts as running)."""
    return "running" if _app_status(host).get("running") else "stopped"


def _dial(host: str) -> tuple[str, str | None]:
    """(DIAL state, screen id). The screen id appears ~10 s after the app is started and is only valid while the
    state is 'running': a stopped or backgrounded app can keep publishing the id of its previous session."""
    status, body = _http(host, DIAL_PORT, "/ws/apps/YouTube")
    if status != 200:
        return "unknown", None
    state = re.search(rb"<state>([^<]+)</state>", body)
    screen = re.search(rb"<screenId>([^<]+)</screenId>", body)
    return (state.group(1).decode() if state else "unknown"), (screen.group(1).decode() if screen else None)


def _screen_id(host: str) -> str | None:
    """The screen id of a YouTube app that is really up, else None."""
    state, screen = _dial(host)
    return screen if state == "running" else None


def _fresh_app(host: str, wait: float, sleep) -> None:
    """Make sure the YouTube app is running in the foreground; a lingering background instance is closed first.

    After the TV has left the app (video ended, back to art mode) the REST API still says running=true but with
    visible=false, and videos sent to that session are accepted by YouTube and then never played.
    """
    info = _app_status(host)
    if info.get("running"):
        if info.get("visible") and _dial(host)[0] == "running":
            return
        status, _ = _app(host, "DELETE")
        if status != 200:
            raise TVError(f"{host}: the TV refused to close the lingering YouTube app (HTTP {status})")
        deadline = time.monotonic() + min(wait, 15)
        while _app_status(host).get("running") and time.monotonic() < deadline:
            sleep(1)
    status, _ = _app(host, "POST")
    if status != 200:
        raise TVError(f"{host}: the TV refused to start the YouTube app (HTTP {status})")


def youtube_start(host: str, video: str, start: int = 0, *, wait: float = 45.0, session_factory=None, sleep=time.sleep) -> str:
    """Start a video in the TV's YouTube app (starting the app first when needed). Returns the video id.

    Takes up to `wait` seconds: the app needs a while before it accepts a video. Works from art mode.
    """
    video_id = parse_youtube_id(video)
    _fresh_app(host, wait, sleep)

    deadline = time.monotonic() + wait
    screen_id = _screen_id(host)
    while not screen_id and time.monotonic() < deadline:
        sleep(1)
        screen_id = _screen_id(host)
    if not screen_id:
        raise TVError(f"{host}: the YouTube app did not become ready within {wait:.0f} s")

    if session_factory is None:
        from casttube import YouTubeSession as session_factory
    try:
        session_factory(screen_id).play_video(video_id, start_time=str(int(start)))
    except Exception as error:   # casttube raises requests errors and plain exceptions
        raise TVError(f"{host}: YouTube did not accept the video ({error})") from error
    return video_id


def youtube_stop(host: str) -> bool:
    """Close the YouTube app. False when it was not running. The TV then leaves the video; use `hide` to be sure
    the art is back."""
    if youtube_state(host) != "running":
        return False
    status, _ = _app(host, "DELETE")
    if status != 200:
        raise TVError(f"{host}: the TV refused to close the YouTube app (HTTP {status})")
    return True

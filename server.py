"""HTTP service for Home Assistant: put a dashboard on the Frame, take it away again, start YouTube.

Configuration (environment):
  API_KEY          required; sent by the caller as the X-Api-Key header
  TVS              the TVs this service may touch: "kitchen=192.168.1.50,living_room=192.168.1.51" or one bare address.
                   Callers choose by name and can never make the service talk to another address.
  HASS_URL, HASS_TOKEN   Home Assistant, for the calendars and camera stills
  DOORBELL_CAMERA  camera entity for the doorbell dashboard
  FALLBACK_ART     content id to show on `hide` when nothing was recorded (optional)
  TVDASH_STATE     where tokens and "what to go back to" are kept (a volume)
  TVDASH_CONFIG    JSON file with the household settings of the morning board (people, calendars, ...);
                   see config.example.json. Checked at startup.
"""
import datetime as dt
import hmac
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import morning
import tv
import tvdash

log = logging.getLogger("tvdash")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    raise RuntimeError("API_KEY is not set: refusing to start a service that controls a TV without authentication")


def _parse_tvs(spec: str) -> dict[str, str]:
    tvs = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        name, _, host = part.rpartition("=")
        tvs[name or "tv"] = host.strip()
    for host in tvs.values():
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise RuntimeError(f"not a valid TV address: {host!r}")
    return tvs


TVS = _parse_tvs(os.environ.get("TVS") or os.environ.get("TV_HOST") or "")
if not TVS:
    raise RuntimeError("TVS is not set, for example TVS=living_room=192.168.1.50")
DOORBELL_CAMERA = os.environ.get("DOORBELL_CAMERA")
FALLBACK_ART = os.environ.get("FALLBACK_ART") or None

if os.environ.get(morning.CONFIG_ENV):
    morning.config()   # a wrong config should stop the service now, not fail the 06:30 automation

# One job at a time: rendering is heavy, and the TV handles one art request at a time anyway.
_executor = ThreadPoolExecutor(max_workers=1)
_last: dict[str, dict] = {}   # result of the most recent job per TV

app = FastAPI(title="tvdash", docs_url=None, redoc_url=None, openapi_url=None)


def auth(x_api_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_api_key.encode(), API_KEY.encode()):
        raise HTTPException(401, "invalid or missing X-Api-Key")


def _resolve(name: str | None) -> tuple[str, str]:
    if name is None:
        name = next(iter(TVS))
    if name not in TVS:
        raise HTTPException(404, f"unknown tv {name!r}; known: {', '.join(TVS)}")
    return name, TVS[name]


class ShowBody(BaseModel):
    tv: str | None = None
    dashboard: Literal["morning", "doorbell"] = "morning"
    date: dt.date | None = Field(None, description="morning only: the day to show, default today")
    camera: str | None = Field(None, pattern=r"^camera\.[a-z0-9_]+$", description="doorbell only: default $DOORBELL_CAMERA")
    wait: bool = Field(False, description="answer when the TV shows it (15-30 s) instead of right away")


class HideBody(BaseModel):
    tv: str | None = None
    fallback: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]+$")
    wait: bool = False


class YouTubeBody(BaseModel):
    tv: str | None = None
    video: str = Field(min_length=11, max_length=300, description="video id or any YouTube URL")
    start: int = Field(0, ge=0, le=86400, description="start offset in seconds")
    wait: bool = Field(False, description="answer when the video has started (up to ~45 s) instead of right away")


class StopBody(BaseModel):
    tv: str | None = None
    wait: bool = False


def _submit(name: str, action: str, work, wait: bool):
    def job():
        started = time.time()
        try:
            result = {"ok": True, "detail": work()}
        except Exception as error:   # reported through /status and the log; a failed job must not kill the worker
            log.exception("%s on %s failed", action, name)
            result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        result.update(action=action, finished=dt.datetime.now().isoformat(timespec="seconds"), seconds=round(time.time() - started, 1))
        _last[name] = result
        log.info("%s on %s: %s", action, name, result)
        return result

    future = _executor.submit(job)
    if not wait:
        return JSONResponse({"accepted": True, "tv": name, "action": action}, status_code=202)
    result = future.result(timeout=180)
    return JSONResponse(result, status_code=200 if result["ok"] else 502)


def _need_hass() -> None:
    if not (os.environ.get("HASS_URL") and os.environ.get("HASS_TOKEN")):
        raise HTTPException(503, "HASS_URL and HASS_TOKEN are not configured")


def _need_config() -> None:
    if not os.environ.get(morning.CONFIG_ENV):
        raise HTTPException(503, f"{morning.CONFIG_ENV} is not configured (the morning board needs it)")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/show", dependencies=[Depends(auth)])
def show(body: ShowBody):
    """Render a dashboard and put it on the TV. The art that was showing is remembered for /hide."""
    name, host = _resolve(body.tv)
    camera = body.camera or DOORBELL_CAMERA
    if body.dashboard == "morning":
        _need_config()
    if body.dashboard == "morning" or camera:
        _need_hass()
    out = tvdash.OUT_DIR / f"{name}.png"

    def work():
        if body.dashboard == "morning":
            tvdash.make_morning(out, body.date)
        else:
            tvdash.make_doorbell(out, camera, os.environ.get("HASS_URL"))
        return {"image": tv.show_image(host, out.read_bytes()), "dashboard": body.dashboard}

    return _submit(name, f"show {body.dashboard}", work, body.wait)


@app.post("/hide", dependencies=[Depends(auth)])
def hide(body: HideBody):
    """Remove our dashboard and put the previous art back (also leaves a YouTube video: the TV returns to art mode)."""
    name, host = _resolve(body.tv)
    return _submit(name, "hide", lambda: tv.hide(host, fallback=body.fallback or FALLBACK_ART), body.wait)


@app.post("/youtube", dependencies=[Depends(auth)])
def youtube(body: YouTubeBody):
    """Start a YouTube video on the TV, also from art mode. The app needs ~15 s to start, so this works in the
    background like /show. Use /youtube/stop or /hide afterwards to go back to the art."""
    name, host = _resolve(body.tv)
    try:
        tv.parse_youtube_id(body.video)   # a bad link is answered at once, not discovered in the background
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _submit(name, "youtube", lambda: {"started": tv.youtube_start(host, body.video, body.start)}, body.wait)


@app.post("/youtube/stop", dependencies=[Depends(auth)])
def youtube_stop(body: StopBody):
    """Close the YouTube app. Call /hide as well to be sure the dashboard or earlier art is back."""
    name, host = _resolve(body.tv)
    return _submit(name, "youtube stop", lambda: {"stopped": tv.youtube_stop(host)}, body.wait)


@app.get("/art", dependencies=[Depends(auth)])
def art(tv_name: str | None = None):
    """The art on the TV, to pick a FALLBACK_ART (our own uploads are marked)."""
    _, host = _resolve(tv_name)
    try:
        return _executor.submit(tv.list_art, host).result(timeout=60)
    except tv.TVError as error:
        raise HTTPException(502, str(error)) from error


@app.get("/status", dependencies=[Depends(auth)])
def status():
    result = {}
    for name, host in TVS.items():
        state = tv.load_state(host)
        try:
            youtube_state = tv.youtube_state(host)
        except (tv.TVError, ValueError) as error:
            youtube_state = f"unknown ({error})"
        result[name] = {
            "address": host,
            "dashboard_showing": state["showing"],
            "will_restore": (state["previous"] or {}).get("content_id"),
            "youtube": youtube_state,
            "last_job": _last.get(name),
        }
    return result

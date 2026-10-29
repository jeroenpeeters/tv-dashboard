# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Dashboards as artwork on a Samsung The Frame, driven by Home Assistant (HA). Two parts:

- **CLI** (`tvdash.py`): render a dashboard, show it on the TV, take it away, start YouTube.
- **Service** (`server.py`, `Dockerfile`): the same as an HTTP API that HA calls from automations. It runs as a container next to HA (docker-compose) or on Kubernetes through the Helm chart in `charts/tvdash`. Examples for all sides are in `deploy/`.

The repository is **public**: see "Publishing" below before adding anything. Tests: `tests/` (stdlib `unittest`, no network, no TV). There is no linter.

## Commands

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests                 # tv.py against a fake art channel, server.py with fakes

.venv/bin/python tvdash.py render                              # dashboard/index.html -> out/dashboard.png (3840x2160)
.venv/bin/python tvdash.py render --url http://ha:8123/lovelace/tv   # screenshot another page instead
.venv/bin/python tvdash.py all --host 192.168.1.50 --camera camera.front_door \
    --hass-url https://ha.example.com --env-file .env         # with doorbell still (HASS_TOKEN from the gitignored `.env`)
.venv/bin/python tvdash.py morning --config config.local.json [--date YYYY-MM-DD] [--host 192.168.1.50] [--print-data] \
    --hass-url https://ha.example.com --env-file .env         # morning board; pushes only with --host
.venv/bin/python tvdash.py push --host 192.168.1.50            # upload out/dashboard.png and show it
.venv/bin/python tvdash.py hide --host 192.168.1.50 [--fallback MY_F0001]   # dashboard away, previous art back
.venv/bin/python tvdash.py art --host 192.168.1.50             # list the art on the TV (to choose a --fallback)
.venv/bin/python tvdash.py wake --host 192.168.1.50            # wake the TV from standby (Wake-on-LAN), waits until it answers
.venv/bin/python tvdash.py youtube --host 192.168.1.50 --video <id or URL> [--start 30]
.venv/bin/python tvdash.py youtube-stop --host 192.168.1.50
.venv/bin/python mockups.py                                    # ten made-up boards (weather types, bins, hand-over days) -> out/mockups/, no HA needed

docker build -t tvdash .                                       # ~80 s, image ~1.4 GB (Chromium)
docker run -d -p 18080:8080 -e API_KEY=… -e TVS=kitchen=192.168.1.50 -e HASS_URL=… -e HASS_TOKEN=… \
    -e TVDASH_CONFIG=/config/tvdash.json -v $PWD/config.local.json:/config/tvdash.json:ro -v tvdash-data:/data tvdash

helm lint charts/tvdash --strict -f charts/tvdash/ci/test-values.yaml        # what CI runs
helm template x charts/tvdash -f charts/tvdash/ci/test-values.yaml
git tag v1.2.3 && git push origin v1.2.3                                       # release: image + chart to ghcr.io (see Publishing)
```

`config.local.json` (gitignored) is the real household config; `config.example.json` is the public, made-up one that tests and the chart docs use.

## Service (HTTP API)

Auth: header `X-Api-Key` = `$API_KEY`; the service refuses to start without it. `TVS` (`name=address,…`) is the allowlist of TVs: callers pick one by name (`"tv": "kitchen"`, default the first) and can never make the service talk to another address. All bodies are JSON. Environment variables are documented at the top of `server.py`; `TVDASH_CONFIG` (the morning board's household settings) is validated at startup, so a wrong file stops the service instead of failing the first automation.

- `POST /show {dashboard: "morning"|"doorbell", date?, camera?, tv?, wait?}`: render and show (`morning` answers 503 without `TVDASH_CONFIG` or HA settings). Answers 202 at once and works in the background (~5 s; `wait: true` blocks and returns the result). Jobs run one at a time, in order.
- `POST /hide {tv?, fallback?, wait?}`: remove our dashboard and put the earlier art back.
- `POST /youtube {video, start?, tv?, wait?}` / `POST /youtube/stop {tv?, wait?}`: background jobs like `/show` (starting takes ~15–45 s because the app needs time; a bad link is refused with 400 at once). After a video, `/hide` makes sure the art is back.
- `GET /status` (per TV: dashboard showing, art to restore, YouTube state, last job incl. error), `GET /art` (art on the TV, ours marked), `GET /health` (no auth).

## Architecture

- `tv.py`: everything that talks to the TV. `show_image` / `hide` / `list_art` use the `samsungframetv` package (the NickWaterton fork; the import name is `samsungframetv`, not `samsungtvws`), the `youtube_*` functions use the TV's REST API, DIAL (read-only) and `casttube`. Callers can pass a fake `art` object, which is what `tests/test_tv.py` does.
- `tvdash.py`: rendering (`render`, `make_morning`, `make_doorbell`) and the CLI. `render` drives Playwright with the system Chrome (`channel="chrome"`); in the container `TVDASH_BROWSER_CHANNEL=""` selects Playwright's own Chromium. Viewport 1920x1080 at 2x scale. Paths come from `TVDASH_OUT` (rendered files) and, in `tv.py`, `TVDASH_STATE` (default `.state/`, `/data` in the container).
- `server.py`: FastAPI, see above. Only one render/TV job runs at a time (single-thread executor).
- `morning.py`: the morning board's data and its settings (`Config`, `parse_config`, cached `config()` from `$TVDASH_CONFIG`). Nothing about a household is hardcoded in the code: people, calendars, hand-over markers, bins calendar, bag rules, news feed and timezone all come from that JSON file. The clothing thresholds stay constants at the top.
- `charts/tvdash`: the Helm chart (one replica, `Recreate`, PVC for `/data`, ConfigMap from `config:`, Secret only through `secret.existingSecret` or inline values, strict `values.schema.json`, read-only root filesystem with an `emptyDir` on `/tmp`). `charts/tvdash/ci/test-values.yaml` holds the made-up values CI renders with.

### Waking the TV (`tv.py`)

A Frame that was switched off (black screen) is in deep standby: it does not answer on the network at all (seen in production: the 06:30 `show` died after 41 s on a connect timeout to port 8002, and the board only appeared after someone woke the TV by hand). So `show_image` and `youtube_start` call `wake(host)` first: if the REST API (`http://<tv>:8001/api/v2/`) does not answer or says `PowerState != "on"`, it sends a Wake-on-LAN magic packet every 5 s (up to 75 s), waits for `on` and then 5 s more for the art channel. `hide` deliberately does not wake the TV: lighting a screen at 09:00 just to put art back is worse than leaving it dark.

The MAC address is not configured: it is read from the TV (`device.wifiMac`) whenever the TV answers and kept in `STATE_DIR/mac-<host>.txt`; a TV that has never answered cannot be woken (clear error). From a pod a broadcast stays inside the pod network, so the packet also goes unicast to the TV and to `a.b.c.255`; `hostNetwork` makes the plain broadcast work. The TV needs "Power on with Mobile" (Settings > General > Network > Expert Settings). After a wake the recorded `previous.artmode` is forced to `on`, so `hide` returns to art instead of the channel the TV booted into. A TV that answers but keeps reporting `standby` (2025 Frames do that in art mode) is let through. Covered by tests with a fake sleeping TV; **not yet verified on the real TV in standby** (it was on when this was written).

### Showing and restoring art (`tv.py`)

- State per TV in `STATE_DIR/state-<host>.json`: `uploads` (our content ids; **only these are ever deleted**, the TV also holds the user's own `MY_F…` photos), `previous` (what to go back to) and `showing`. An older `uploads-<host>.json` (a bare list) is read as `showing: True, previous: None`.
- The first `show` after a `hide` records `previous` (current content id, art mode, slideshow settings) **before** touching the TV, and switches a running slideshow off (it would rotate our image away). Further `show`s replace our upload and keep the original `previous`. Our own image is never recorded as `previous`.
- `hide` selects the previous art, resumes the slideshow, deletes our uploads and restores art mode (also leaves TV mode after YouTube). With nothing recorded it uses `fallback` (or `$FALLBACK_ART`), and without that it only deletes our image and the TV picks its own art.
- `set_artmode(True)` times out if the TV is already in art mode, so it is only called after checking `get_artmode()`.
- Verified on the real TV (container, fresh state): show recorded the current art, hide restored it and removed the upload. The slideshow restore and the TV-mode restore are only covered by the fake in `tests/` (the TV had its slideshow off and was in art mode).

### YouTube

Verified on the real Frame (a video played): (1) start the app with `POST http://<tv>:8001/api/v2/applications/111299001912` (REST; works **from art mode**, needs no pairing; its body is junk and the `samsungframetv` helper `rest_app_run` fails to parse it although the launch succeeds, so `tv.py` does the request itself); (2) poll the DIAL `GET http://<tv>:8080/ws/apps/YouTube` until it contains a `<screenId>` (appears **~15 s** after the start); (3) send the video with `casttube.YouTubeSession(screen_id).play_video(id)`, which talks to YouTube's cast service (so the service host needs internet). Stop with `DELETE …/applications/111299001912`. This is the route of the HA integration `ha-samsungtv-smart`.

What does **not** work here, so do not try it again: `POST …:8080/ws/apps/YouTube` (DIAL launch) answers **403** in every variant and in art mode and TV mode alike; `run_app` over the WebSocket remote does not start the app (the remote channel also needs an "Allow" on the TV, `ms.channel.timeOut` otherwise). Ids/URLs are validated (`parse_youtube_id`) before anything reaches the TV.

**Stale background session (found in production):** after the TV has left the app (video ended, back to art mode) the REST status is `running: true, visible: false`, DIAL says `<state>stopped</state>` and still publishes the `screenId` of the old session. A video sent to that id is accepted by YouTube (no error, the call returns in ~1 s) and never plays. So "ready" means DIAL `state == running` **and** a `screenId`, and a running-but-invisible app is closed (`DELETE`) and started fresh (`_fresh_app`). On a fresh start DIAL is `running` and the app `visible: true` at once, the `screenId` follows after ~11 s. A start that returns within a second is therefore suspicious.

Observed: after closing the app the TV sometimes returns to art mode by itself and sometimes stays in TV mode, so `hide` checks `get_artmode()` before restoring (calling `set_artmode(True)` while already in art mode times out). The `tv.youtube_start` code path is covered by tests against a fake TV that mimics the stale background session; the start/stop/DIAL timing was probed on the real TV, but the final code has not yet played a video as one call.

### Morning board

`morning.py` collects the data (HA calendar REST API, Open-Meteo for the HA home coordinates) and `tvdash.make_morning` injects it into `dashboard/morning.html` as `window.DATA` through a Playwright init script. All behaviour is tunable through constants at the top of `morning.py`:

- People map to HA calendars through `people` in the config (display name -> calendar entities; an entity id may carry the `calendar.` prefix and is validated, because it ends up in a URL path).
- The optional `kids` config block turns on the hand-over day: in `kids.calendar` an all-day item whose first word is `away_marker` means the kids are away, `home_marker` means here; otherwise they are here on `home_weekdays` (Monday = 0, default Wed/Thu) and on Friday when `home_friday_if_weekend_home` and the coming weekend is a `home_marker` one. A wisseldag is any day whose location differs from yesterday; the board only shows a "Wisseldag naar <away_label|home_label>" row on such a day and never says where the kids are otherwise. The marker items are hidden from the agenda list. Without the block there is no hand-over logic at all.
- Bins come from the calendar named by `bins_calendar` (optional; all-day items; "Gfte" is shown as GFT) and only those collected on the displayed date are flagged, as icon + name (GFT / PMD / Papier) with no extra text.
- Bags are matched from today's event titles via `bag_rules` in the config (regex `match` -> `label`; defaults: rugby, zwem, sportdag, gym).
- Weather is deliberately terse: min–max and a one-word condition. The condition is the most common 08–17 h Open-Meteo code (the wettest one when the rain advice triggers), not the daily code, so it agrees with the clothing advice (legs / jacket / rain jacket thresholds, constants in `morning.py`). Open-Meteo only forecasts ~2 weeks ahead, so `build` fails for later dates; it returns a 503 now and then, so `_get_json` retries.
- News: the newest `news_items` (3) of the RSS feed `news_feed` (default: the NOS feed; `null` switches the news off; it is ordered by time, not importance), shown as a plain bullet list over the full width. Quotes around a whole title are stripped (`_unquote`) and titles over `NEWS_MAX_CHARS` are cut at a clause break (`_shorten`). A failing feed just removes the list instead of breaking the board.
- Layout (`dashboard/morning.html`): row 1 = date + min–max and one-word condition; row 2 = the flags on the left (bins, bags; they wrap) and the clothing pills on the right under the weather; then the agenda as three translucent cards over the full width (items clamp at 3 lines; on a wisseldag the Kids card starts with a highlighted row, set as `wissel` on that person in the data by `build`); then the news bullets. The theme is dark on purpose (the board is seen right after waking up): light text on a dimmed sky, and the sun / cloud / rain / snow / fog scene is generated from the weather condition at low opacity so it never fights the text. The gradient is tinted cold-blue to warm-amber by the mean temperature (palette tables near the top of the script). Emoji in the pills sit on a light disc, because dark emoji (jacket, jeans) vanish on the dark pills. Rain and snow positions are seeded by the date, so the same day always renders the same. The agenda outranks the news: the last script step first removes the whole news block when any agenda card overflows (and the empty grid row with it), and only if that is not enough removes items one at a time until a "+ n meer" line fits inside the card; keep that step last, because pills and news change the card height.
- The board has no clock and is a still image: render it the evening before or early morning, with `--date` for another day.

### Doorbell dashboard

`dashboard/index.html`: self-contained page (clock, date, Open-Meteo weather; `?lat=&lon=` overrides the Amsterdam default). It sets `body[data-ready]` when its fetch settles; `render` waits for that. `--camera` / `make_doorbell` fetches a still via HA `/api/camera_proxy/<entity>` and passes it as `?cam=<file URI>`; the page then switches to the camera-plus-2x2 layout.

## Publishing (the repository is public)

Nothing personal and no secret may be tracked. That is why:
- all household specifics live in the config file (`TVDASH_CONFIG`), never in code, tests, docs, mockups or examples. Examples use made-up names (Anna, Ben), `example.com`, `camera.front_door` and `192.168.1.x`;
- real values live in gitignored files: `.env`, `config.local.json`, `*.local.yaml`, `CLAUDE.local.md`, `.state/`, `out/`, `.idea/`, and the Dutch hand-over note `frame-tv-handoff.md`. Keep new personal files matching those patterns, and never put a token, address or calendar name into a tracked file, a test or a commit message;
- the chart takes secrets only through `secret.existingSecret` (or inline values the user encrypts themselves). The API key is the only gate in front of a service that controls a TV and reads calendars.

Release flow (`.github/workflows/release.yml`): a tag `vX.Y.Z` (or `vX.Y.Z-rc.1`) on `main` runs the CI workflow, then builds and pushes `ghcr.io/<owner>/tvdash:X.Y.Z` (+ `X.Y`, no `latest`), then packages the chart with `--version X.Y.Z --app-version X.Y.Z` and pushes it to `oci://ghcr.io/<owner>/charts` (package `charts/tvdash`; a different name than the image, on purpose), signs both keyless with cosign, and creates the GitHub release. `Chart.yaml` and `image.repository` in the repository are placeholders (`0.0.0`, `ghcr.io/OWNER/tvdash`): the workflow patches `OWNER` in the packaged chart, and the chart refuses to render with the placeholder. Rendering the chart with the release steps against a local `registry:2` (`helm push --plain-http`) was tested; the real GHCR push and cosign signing have not been run yet.

Workflow conventions: every action is pinned to a commit SHA with the version in a comment (Dependabot keeps them current); workflow permissions are `contents: read` at the top and widened per job (`packages: write` and `id-token: write` only in the publishing jobs); a called workflow cannot get more permissions than its caller, so `release.yml`'s `checks` job must grant what `ci.yml` asks for; tag values go through `env:`, never straight into a script. Check workflows with `actionlint`.

## Environment facts

- The art channel works **without a token** on the tested Frame: no `token-<host>.txt` is ever written, and a container on another address (docker NAT) connected without any approval. Other TVs/settings may hand out a token; it is then kept in `STATE_DIR`.
- The container image installs `fonts-inter` and `fonts-noto-color-emoji`; the board renders identically to a local render with them (checked by comparing the render). The image also runs fine read-only, with all capabilities dropped and uid 1000, which is how the chart runs it (Chromium only needs `/tmp`).
- Site-specific facts (addresses, entity names, which TV is in use) are in `CLAUDE.local.md`, which is not part of the repository.

## Original background

- Controlling the TV does **not** require developer mode. The TV exposes a local WebSocket API on ports 8001/8002; developer mode is only for sideloading Tizen `.wgt` apps (approach 2 below, not started).
- Gotchas: the TV rejects WebSocket connections from outside its own subnet (on an IoT VLAN the HA host needs source-NAT into that segment; in Kubernetes that is what `hostNetwork` is for); HA reports art mode as "on", so automations that only check state misfire.
- Approach 1, **dashboard as artwork** (implemented): screenshot a page, scale to 3840x2160, upload through the art API, delete the old upload. Static image, no developer mode. Approach 2, a sideloaded Tizen kiosk app, would be live but fragile (host-IP binding, certificate, remote-control navigation).

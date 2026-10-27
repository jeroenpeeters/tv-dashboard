# tvdash

Dashboards as artwork on a Samsung The Frame, driven by Home Assistant.

tvdash renders a page (a morning board with everyone's agenda, bins, weather and clothing advice, or a
clock with the doorbell camera) to a 4K image, uploads it to the Frame as art, and puts the earlier art back
when Home Assistant says so. It can also start a YouTube video on the TV, for example a fireplace.

It runs as a small HTTP service next to Home Assistant: one container, one Helm chart.

```
Home Assistant ── POST /show {"dashboard":"morning"} ──▶ tvdash ──▶ Samsung Frame (art mode API)
                                  X-Api-Key                 │
                  POST /hide, /youtube, /youtube/stop       └──▶ Home Assistant (calendars, camera)  + Open-Meteo + RSS
```

Nothing in this repository is specific to one household. Everything personal (people, calendars, the TV
addresses) is configuration, supplied at deploy time.

## The service

All calls need the header `X-Api-Key`. Bodies are JSON.

| Call | What it does |
| --- | --- |
| `POST /show {dashboard: "morning"\|"doorbell", date?, camera?, tv?, wait?}` | Render and show. Answers at once (202), works in the background (~5-30 s). Remembers the art that was showing. |
| `POST /hide {tv?, fallback?, wait?}` | Remove the dashboard and put the earlier art back (also after YouTube). |
| `POST /youtube {video, start?, tv?, wait?}` | Start a YouTube video (id or any URL), also from art mode. The app needs 15-45 s. |
| `POST /youtube/stop {tv?, wait?}` | Close the YouTube app. |
| `GET /status`, `GET /art` | State per TV; the art on the TV (to pick a fallback). |
| `GET /health` | Liveness, no key needed. |

Safety properties: the service refuses to start without `API_KEY`; callers choose a TV by *name* from an
allowlist (`TVS`) and can never make it talk to another address; it only ever deletes art that it uploaded
itself (the TV's own photos are never touched); video links are validated before anything reaches the TV.

### Configuration

Environment variables:

| Variable | |
| --- | --- |
| `API_KEY` | required; what Home Assistant sends as `X-Api-Key` |
| `TVS` | required; `name=address,name=address`, the first is the default |
| `HASS_URL`, `HASS_TOKEN` | Home Assistant URL and a long-lived access token (calendars, camera stills) |
| `TVDASH_CONFIG` | path of the JSON file with the morning board's settings (below); checked at startup |
| `DOORBELL_CAMERA` | camera entity for the doorbell dashboard |
| `FALLBACK_ART` | art id to show on `/hide` when no earlier art was recorded |
| `TVDASH_STATE` | where per-TV state is kept (a volume; `/data` in the image) |

The morning board's settings are in a JSON file, see [`config.example.json`](config.example.json): the
people and their Home Assistant calendars, an optional bins calendar, an optional hand-over day for kids
who live in two homes, "pack this bag" rules matched on event titles, the timezone and the news feed. The
file is validated strictly (typos and unsafe entity ids are rejected), and without it only the doorbell
dashboard and YouTube work.

## Run it

### Docker / docker-compose

```
docker run -d -p 8080:8080 -e API_KEY=… -e TVS=kitchen=192.168.1.50 \
  -e HASS_URL=http://homeassistant:8123 -e HASS_TOKEN=… \
  -e TVDASH_CONFIG=/config/tvdash.json -v "$PWD/my-config.json":/config/tvdash.json:ro \
  -v tvdash-data:/data ghcr.io/OWNER/tvdash:1.0.0
```

A compose example is in [`deploy/docker-compose.example.yml`](deploy/docker-compose.example.yml).

### Kubernetes (Helm)

```
helm install tvdash oci://ghcr.io/OWNER/charts/tvdash --version 1.0.0 -n tvdash --create-namespace -f my-values.yaml
```

Minimal values (see [`charts/tvdash/values.yaml`](charts/tvdash/values.yaml) for everything; the values are
validated against a schema, so a typo fails at install time):

```yaml
tvs:
  - {name: kitchen, address: 192.168.1.50}
hass: {url: https://ha.example.com}
secret: {existingSecret: tvdash-secrets}     # keys: api-key, hass-token
config: {…}                                  # the same as config.example.json, as YAML
ingress: {enabled: true, className: nginx, hosts: [{host: tvdash.example.com}]}
```

**Secrets.** Create the Secret yourself (SOPS, External Secrets, sealed secrets, …) with the keys `api-key`
(`openssl rand -hex 32`) and `hass-token`, and reference it with `secret.existingSecret`. Never put them in
a values file that is committed in the clear. The chart can create the Secret from `secret.apiKey` /
`secret.hassToken`, but then they are part of your release values.

**Networking.** Two things must be reachable:
- *The TV from the pod.* The Frame only accepts connections from its own subnet. Most clusters source-NAT
  pod traffic to the node address, which works when the nodes are in the TV's subnet. If the TV is on another
  VLAN, or your CNI does not NAT, set `hostNetwork: true` (port 8080 must then be free on the node) and make
  sure the node can reach the TV on ports 8001, 8002 and 8080.
- *The service from Home Assistant.* Inside the cluster use `http://<release>-tvdash.<namespace>.svc:8080`.
  For Home Assistant outside the cluster use the ingress, or `service.type: LoadBalancer`/`NodePort`. The API
  key is the only gate: use TLS on anything that leaves the cluster.

The pod runs as a non-root user with a read-only root filesystem and no capabilities. It keeps one small
volume (`persistence`, 100 Mi) with "which art to go back to"; without it a pod restart forgets that.
There is exactly one replica, by design.

### Flux

[`deploy/flux/tvdash.example.yaml`](deploy/flux/tvdash.example.yaml) has an `OCIRepository` (semver range
over the chart) and a `HelmRelease`, including the optional cosign verification of the chart signature.

### Home Assistant

[`deploy/homeassistant.example.yaml`](deploy/homeassistant.example.yaml) has the `rest_command`s, a script
pair for a fireplace video, and the automations that show the morning board at 06:30 and remove it at 09:00.

## Releases

Pushing a tag `vX.Y.Z` on `main` runs the checks and, when they pass, publishes:

- the image `ghcr.io/<owner>/tvdash:X.Y.Z` (and `:X.Y`; there is deliberately no `latest`),
- the chart `oci://ghcr.io/<owner>/charts/tvdash` at version `X.Y.Z`, whose default image is the one above,
- cosign (keyless) signatures for both, and a GitHub release.

```
git tag v1.0.0 && git push origin v1.0.0
cosign verify ghcr.io/<owner>/tvdash:1.0.0 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/<owner>/<repo>/\.github/workflows/release\.yml@refs/tags/v.+$'
```

New packages on ghcr.io start private. Make them public (package settings → *Change visibility*) or give
the cluster a pull secret (`imagePullSecrets`, and `secretRef` on the Flux `OCIRepository`).

CI (`.github/workflows/ci.yml`) runs on every pull request: the Python tests, `helm lint --strict` and
template checks, a build of the image with a smoke test under the same restrictions the chart applies, and a
secret scan. Platforms: `linux/amd64`; for ARM nodes set `PLATFORMS` in `release.yml`.

## Developing

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests          # no network, no TV needed
.venv/bin/python mockups.py                             # ten made-up boards in out/mockups/, no Home Assistant needed
```

Run the CLI against your own setup with the gitignored files `.env` (`HASS_TOKEN=…`) and
`config.local.json`: `python tvdash.py morning --config config.local.json --hass-url … --env-file .env
[--host <tv>]`. See `python tvdash.py --help`.

### Keeping the repository clean

The repository is meant to be public, so keep these out of every tracked file, test and commit message:
tokens and keys, your real hostnames and addresses, calendar and camera entity names, family names.
`.gitignore` already covers `.env*`, `*.local.*`, `.state/`, `out/` and IDE folders; put personal files under
those names. Switch on **secret scanning and push protection** for the GitHub repository (Settings → Code
security), restrict the default workflow token to read-only (Settings → Actions → General), and protect
`main`.

## How the Frame is driven

The art API of the Frame (ports 8001/8002) is used through the
[NickWaterton fork of `samsung-tv-ws-api`](https://github.com/NickWaterton/samsung-tv-ws-api), pinned to a
commit. YouTube is started through the TV's REST API and DIAL service, and the video is sent with
[`casttube`](https://pypi.org/project/casttube/), the same route as the Home Assistant integration
`ha-samsungtv-smart`. Tested on a 2021 Frame; the art channel needed no pairing token there. Other models and
firmware may differ.

"""Collect everything the morning board shows: calendars, bins, hand-over day, bags, weather and clothing advice."""
import json
import os
import re
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG_ENV = "TVDASH_CONFIG"   # path of the JSON file with everything that is specific to one household


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Kids:
    """Where the kids are is derived from the hand-over markers in one calendar; see `_kids_home`."""
    person: str                            # the card that shows the hand-over day at its top
    calendar: str
    away_marker: str                       # an all-day item starting with this word: the kids are away
    home_marker: str                       # ... starting with this word: the kids are here
    home_weekdays: tuple[int, ...] = (2, 3)            # Monday = 0; here on these days without any item
    home_friday_if_weekend_home: bool = True
    away_label: str = "mama"               # "Wisseldag naar <label>"
    home_label: str = "papa"


@dataclass(frozen=True)
class Config:
    people: tuple[tuple[str, tuple[str, ...]], ...]    # display name -> HA calendar entities
    timezone: str = "Europe/Amsterdam"
    bins_calendar: str | None = None
    kids: Kids | None = None
    # Calendar title -> thing to pack. Matched against today's events of every calendar.
    bag_rules: tuple[tuple[re.Pattern, str], ...] = ()
    # News: the newest items of this RSS feed, each cut to one short line. The feed is ordered by time, not importance.
    news_feed: str | None = "https://feeds.nos.nl/nosnieuwsalgemeen"
    news_items: int = 3

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


DEFAULT_BAG_RULES = [
    {"match": "rugby", "label": "Rugbyspullen"},
    {"match": "zwem", "label": "Zwemspullen"},
    {"match": "sportdag", "label": "Sportkleding aan"},
    {"match": "gym", "label": "Gymspullen"},
]
NEWS_MAX_CHARS = 100  # one full-width line; longer titles are cut at a clause break

# Clothing thresholds, on the daytime (08-17 h) forecast.
SHORTS_FROM_C = 20            # daytime max
NO_JACKET_FROM_C = 18         # daytime mean apparent temperature
THICK_JACKET_BELOW_C = 8
RAIN_MM = 1.0                 # daytime sum that counts as "rain"
RAIN_PROB = 50

_ENTITY = re.compile(r"[a-z0-9_]+")   # ends up in a URL path, so nothing else is accepted


def _calendar(value, where: str) -> str:
    name = str(value).removeprefix("calendar.")
    if not _ENTITY.fullmatch(name):
        raise ConfigError(f"{where}: {value!r} is not a calendar entity id")
    return name


def parse_config(raw: dict) -> Config:
    """Validate the contents of the config file. See config.example.json for the shape."""
    known = {"timezone", "people", "bins_calendar", "kids", "bag_rules", "news_feed", "news_items"}
    if unknown := set(raw) - known:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    people = []
    for i, entry in enumerate(raw.get("people") or []):
        name, calendars = entry.get("name"), entry.get("calendars")
        if not name or not calendars:
            raise ConfigError(f"people[{i}] needs a name and at least one calendar")
        people.append((str(name), tuple(_calendar(c, f"people[{i}]") for c in calendars)))

    kids = None
    if raw.get("kids"):
        k = dict(raw["kids"])
        missing = {"person", "calendar", "away_marker", "home_marker"} - set(k)
        if missing:
            raise ConfigError(f"kids is missing: {', '.join(sorted(missing))}")
        k["calendar"] = _calendar(k["calendar"], "kids.calendar")
        if "home_weekdays" in k:
            k["home_weekdays"] = tuple(int(d) for d in k["home_weekdays"])
            if not all(0 <= d <= 6 for d in k["home_weekdays"]):
                raise ConfigError("kids.home_weekdays: use 0 (Monday) to 6 (Sunday)")
        try:
            kids = Kids(**k)
        except TypeError as error:
            raise ConfigError(f"kids: {error}") from error
        if kids.person not in {n for n, _ in people}:
            raise ConfigError(f"kids.person {kids.person!r} is not one of the people")

    rules = raw.get("bag_rules", DEFAULT_BAG_RULES)
    try:
        bag_rules = tuple((re.compile(r["match"], re.I), r["label"]) for r in rules)
    except (KeyError, TypeError, re.error) as error:
        raise ConfigError(f"bag_rules: each rule needs a valid regex 'match' and a 'label' ({error})") from error

    feed = raw.get("news_feed", Config.news_feed)
    if feed is not None and not str(feed).startswith(("https://", "http://")):
        raise ConfigError("news_feed must be an http(s) URL, or null to switch the news off")
    timezone = raw.get("timezone", Config.timezone)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"timezone {timezone!r} is unknown") from error
    bins = raw.get("bins_calendar")
    return Config(tuple(people), timezone, _calendar(bins, "bins_calendar") if bins else None, kids,
                  bag_rules, feed, int(raw.get("news_items", Config.news_items)))


@lru_cache(maxsize=1)
def config() -> Config:
    """The settings from the file named by $TVDASH_CONFIG. Raises ConfigError when it is missing or wrong."""
    path = os.environ.get(CONFIG_ENV)
    if not path:
        raise ConfigError(f"{CONFIG_ENV} is not set: point it to a JSON file (see config.example.json)")
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    cfg = parse_config(raw)
    if not cfg.people:
        raise ConfigError("people is empty: the board needs at least one person with a calendar")
    return cfg


def _env(key: str) -> str:
    return os.environ[key]


def _ha(path: str):
    req = urllib.request.Request(
        _env("HASS_URL").rstrip("/") + path, headers={"Authorization": f"Bearer {_env('HASS_TOKEN')}"}
    )
    return json.load(urllib.request.urlopen(req, timeout=30))


def _fetch(url: str, tries: int = 3, headers: dict | None = None) -> bytes:
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=30).read()
        except urllib.error.URLError:  # includes HTTPError, e.g. a transient 503
            if attempt == tries - 1:
                raise
            time_module.sleep(2 * (attempt + 1))


def _get_json(url: str):
    return json.loads(_fetch(url))


def _events(calendar: str, day: date, days: int = 1) -> list[dict]:
    """Events overlapping [day, day+days), normalised to {'summary', 'all_day', 'start', 'end'} in local time."""
    tz = config().tz
    start = datetime.combine(day, time.min, tz)
    end = start + timedelta(days=days)
    q = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
    out = []
    for e in _ha(f"/api/calendars/calendar.{calendar}?{q}"):
        if "date" in e["start"]:
            s = datetime.combine(date.fromisoformat(e["start"]["date"]), time.min, tz)
            f = datetime.combine(date.fromisoformat(e["end"]["date"]), time.min, tz)
            out.append({"summary": e["summary"].strip(), "all_day": True, "start": s, "end": f})
        else:
            s = datetime.fromisoformat(e["start"]["dateTime"]).astimezone(tz)
            f = datetime.fromisoformat(e["end"]["dateTime"]).astimezone(tz)
            out.append({"summary": e["summary"].strip(), "all_day": False, "start": s, "end": f})
    return sorted(out, key=lambda e: (not e["all_day"], e["start"]))


def _on(e: dict, day: date) -> bool:
    return e["start"].date() <= day < (e["end"].date() if e["all_day"] else e["end"].date() + timedelta(days=1))


def _first_word(summary: str) -> str:
    return summary.split()[0] if summary.split() else ""


def _kids_markers(day: date) -> set[str]:
    kids = config().kids
    return {_first_word(e["summary"]) for e in _events(kids.calendar, day)
            if e["all_day"] and _on(e, day) and _first_word(e["summary"]) in (kids.away_marker, kids.home_marker)}


def _kids_home(day: date) -> bool:
    kids = config().kids
    marks = _kids_markers(day)
    if kids.away_marker in marks:
        return False
    if kids.home_marker in marks or day.weekday() in kids.home_weekdays:
        return True
    return (kids.home_friday_if_weekend_home and day.weekday() == 4
            and kids.home_marker in _kids_markers(day + timedelta(days=1)))


def _kids_change(day: date) -> str | None:
    """The label of where the kids go / come to when their location today differs from yesterday, else None."""
    kids = config().kids
    if kids is None:
        return None
    before, today = _kids_home(day - timedelta(days=1)), _kids_home(day)
    return None if before == today else (kids.home_label if today else kids.away_label)


def _bin_name(summary: str) -> str:
    first = summary.split()[0] if summary else summary
    return "GFT" if first.lower() == "gfte" else first


def _bins(day: date) -> list[str]:
    calendar = config().bins_calendar
    return [_bin_name(e["summary"]) for e in _events(calendar, day) if _on(e, day)] if calendar else []


def _agenda(day: date) -> list[dict]:
    cfg, people = config(), []
    for name, cals in cfg.people:
        items = []
        for cal in cals:
            for e in _events(cal, day):
                if not _on(e, day):
                    continue
                title = re.sub(r"^algemeen - ", "", e["summary"])
                if (cfg.kids and cal == cfg.kids.calendar and e["all_day"]
                        and _first_word(title) in (cfg.kids.away_marker, cfg.kids.home_marker)):
                    continue  # shown as the hand-over banner instead
                items.append({"title": title, "time": None if e["all_day"] else e["start"].strftime("%H:%M"),
                              "sort": (not e["all_day"], e["start"].isoformat())})
        items.sort(key=lambda i: i["sort"])
        people.append({"name": name, "items": [{"title": i["title"], "time": i["time"]} for i in items]})
    return people


def _bags(day: date, people: list[dict]) -> list[str]:
    bags = []
    for p in people:
        for i in p["items"]:
            for rx, label in config().bag_rules:
                if rx.search(i["title"]) and label not in bags:
                    bags.append(label)
    return bags


def _weather(day: date) -> dict:
    cfg = _ha("/api/config")
    q = urllib.parse.urlencode({
        "latitude": cfg["latitude"], "longitude": cfg["longitude"], "timezone": config().timezone,
        "start_date": day.isoformat(), "end_date": day.isoformat(),
        "hourly": "temperature_2m,apparent_temperature,precipitation,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
    })
    w = _get_json("https://api.open-meteo.com/v1/forecast?" + q)
    h, d = w["hourly"], w["daily"]
    hours = range(8, 17)
    pick = lambda k: [h[k][i] for i in hours]
    codes = Counter(pick("weather_code"))
    # Most common daytime condition (ties go to the more severe code), so the text agrees with the clothing advice.
    code = max(codes, key=lambda c: (codes[c], c))
    temp, feels, rain, prob = pick("temperature_2m"), pick("apparent_temperature"), pick("precipitation"), pick("precipitation_probability")
    wet = sum(rain) >= RAIN_MM or max(prob) >= RAIN_PROB
    if wet and code < 51:  # rain advice but a dry-looking mode: show the wettest daytime condition instead
        code = max(pick("weather_code"))
    mean_feels = sum(feels) / len(feels)

    shorts = max(temp) >= SHORTS_FROM_C
    if mean_feels < THICK_JACKET_BELOW_C:
        jacket = "Dikke jas"
    elif mean_feels < NO_JACKET_FROM_C:
        jacket = "Dunne jas"
    else:
        jacket = "Geen jas"
    return {
        "code": code,
        "min": round(d["temperature_2m_min"][0]), "max": round(d["temperature_2m_max"][0]),
        "legs": "Korte broek" if shorts else "Lange broek",
        "jacket": jacket,
        "umbrella": wet,
    }


NEWS_CLAUSE_BREAKS = (", ", ": ", " – ", " - ", " dat ", " omdat ", " nadat ", " maar ", " terwijl ", " nu ", " na ", " en ")


QUOTE_PAIRS = {"'": "'", '"': '"', "‘": "’", "“": "”", "«": "»"}


def _unquote(title: str) -> str:
    """NOS wraps quoted statements in quotes; drop a pair that surrounds the whole title."""
    open_q = title[:1]
    if open_q in QUOTE_PAIRS and title.endswith(QUOTE_PAIRS[open_q]) and len(title) > 2:
        inner = title[1:-1]
        if open_q not in inner and QUOTE_PAIRS[open_q] not in inner:
            return inner
    return title


def _shorten(title: str) -> str:
    """One short line: keep the title if it fits, else cut at the last clause break that leaves a meaningful start."""
    if len(title) <= NEWS_MAX_CHARS:
        return title
    cuts = [title.rfind(b, 0, NEWS_MAX_CHARS + 1) for b in NEWS_CLAUSE_BREAKS]
    cut = max(cuts)
    if cut >= 28:
        return title[:cut].rstrip(" ,:–-")
    return title[: NEWS_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _news() -> list[str]:
    """Headlines for the board. The board must still render when the feed is down, so failures give an empty list."""
    cfg = config()
    if not cfg.news_feed:
        return []
    try:
        root = ET.fromstring(_fetch(cfg.news_feed, tries=2, headers={"User-Agent": "tv-dashboard"}))
    except (OSError, ET.ParseError):
        return []
    titles = [(i.findtext("title") or "").strip() for i in root.findall("./channel/item")]
    return [_shorten(_unquote(t)) for t in titles if t][:cfg.news_items]


def build(day: date | None = None) -> dict:
    cfg = config()
    day = day or datetime.now(cfg.tz).date()
    people = _agenda(day)
    wissel = _kids_change(day)
    for p in people:
        if cfg.kids and p["name"] == cfg.kids.person and wissel:
            p["wissel"] = wissel
    return {
        "date": day.isoformat(),
        "weekday": day.weekday(),
        "bins": _bins(day),
        "bags": _bags(day, people),
        "people": people,
        "weather": _weather(day),
        "news": _news(),
    }

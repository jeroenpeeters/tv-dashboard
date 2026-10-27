"""Tests for the settings and the calendar logic of morning.py, with a fake calendar instead of Home Assistant."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
import morning  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "config.example.json"
TZ = morning.ZoneInfo("Europe/Amsterdam")


def raw_config(**extra) -> dict:
    base = {
        "people": [{"name": "Anna", "calendars": ["anna"]}, {"name": "Kids", "calendars": ["family_kids"]}],
        "bins_calendar": "waste",
        "kids": {"person": "Kids", "calendar": "family_kids", "away_marker": "Mama", "home_marker": "Papa"},
    }
    return {**base, **extra}


def all_day(summary: str, day: date) -> dict:
    start = datetime.combine(day, datetime.min.time(), TZ)
    return {"summary": summary, "all_day": True, "start": start, "end": start + timedelta(days=1)}


def timed(summary: str, day: date, hour: int) -> dict:
    start = datetime.combine(day, datetime.min.time(), TZ) + timedelta(hours=hour)
    return {"summary": summary, "all_day": False, "start": start, "end": start + timedelta(hours=1)}


class Fake:
    """Patches the configuration and the calendar lookups of morning.py."""

    def __init__(self, events: dict[str, list[dict]], **extra):
        self.cfg, self.events = morning.parse_config(raw_config(**extra)), events

    def __enter__(self):
        def events(calendar, day, days=1):
            return [e for e in self.events.get(calendar, []) if morning._on(e, day)]
        self.patches = [mock.patch.object(morning, "config", return_value=self.cfg),
                        mock.patch.object(morning, "_events", events)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class Settings(unittest.TestCase):
    def test_the_example_config_is_valid(self):
        cfg = morning.parse_config(json.loads(EXAMPLE.read_text()))
        self.assertEqual([name for name, _ in cfg.people], ["Anna", "Ben", "Kids"])
        self.assertEqual(cfg.kids.person, "Kids")

    def test_calendar_ids_may_carry_the_domain_prefix(self):
        cfg = morning.parse_config(raw_config(people=[{"name": "A", "calendars": ["calendar.anna"]}], kids=None))
        self.assertEqual(cfg.people, (("A", ("anna",)),))

    def test_nothing_that_could_alter_the_request_path_is_accepted_as_a_calendar(self):
        for bad in ("../states", "a/b", "a?b=1", "Anna", ""):
            with self.assertRaises(morning.ConfigError, msg=bad):
                morning.parse_config(raw_config(people=[{"name": "A", "calendars": [bad]}], kids=None))

    def test_typos_and_inconsistencies_are_rejected(self):
        for bad in ({"peple": []},
                    {"kids": {"person": "Nobody", "calendar": "x", "away_marker": "a", "home_marker": "b"}},
                    {"kids": {"person": "Kids"}},
                    {"timezone": "Mars/Base"},
                    {"news_feed": "file:///etc/passwd"},
                    {"bag_rules": [{"match": "(", "label": "x"}]}):
            with self.assertRaises(morning.ConfigError, msg=str(bad)):
                morning.parse_config(raw_config(**bad))

    def test_config_needs_a_file_with_people(self):
        morning.config.cache_clear()
        self.addCleanup(morning.config.cache_clear)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            os.environ.pop(morning.CONFIG_ENV, None)
            with self.assertRaisesRegex(morning.ConfigError, "not set"):
                morning.config()
            path = Path(tmp) / "c.json"
            path.write_text("{}")
            os.environ[morning.CONFIG_ENV] = str(path)
            with self.assertRaisesRegex(morning.ConfigError, "people is empty"):
                morning.config()
            morning.config.cache_clear()
            path.write_text("{not json")
            with self.assertRaisesRegex(morning.ConfigError, "not valid JSON"):
                morning.config()

    def test_news_and_bins_and_kids_are_optional(self):
        cfg = morning.parse_config({"people": [{"name": "A", "calendars": ["a"]}], "news_feed": None})
        self.assertIsNone(cfg.kids)
        self.assertIsNone(cfg.bins_calendar)
        self.assertIsNone(cfg.news_feed)


class KidsLocation(unittest.TestCase):
    MON, WED, THU, FRI, SAT, SUN, NEXT_MON = (date(2026, 11, d) for d in (2, 4, 5, 6, 7, 8, 9))

    def test_marker_days_and_the_default_weekdays(self):
        events = {"family_kids": [all_day("Mama weekend", self.SAT), all_day("Mama weekend", self.SUN)]}
        with Fake(events):
            self.assertFalse(morning._kids_home(self.SAT))      # marker: away
            self.assertTrue(morning._kids_home(self.WED))       # default home day
            self.assertTrue(morning._kids_home(self.THU))
            self.assertFalse(morning._kids_home(self.MON))      # no marker, not a home weekday

    def test_friday_is_home_only_when_the_coming_weekend_is_at_home(self):
        with Fake({"family_kids": [all_day("Papa weekend", self.SAT)]}):
            self.assertTrue(morning._kids_home(self.FRI))
        with Fake({"family_kids": [all_day("Mama weekend", self.SAT)]}):
            self.assertFalse(morning._kids_home(self.FRI))

    def test_the_hand_over_day_is_named_after_where_the_kids_go(self):
        away = {"family_kids": [all_day("Mama weekend", self.SAT), all_day("Mama weekend", self.SUN)]}
        with Fake(away):
            self.assertEqual(morning._kids_change(self.FRI), "mama")      # Thursday here, Friday off to mama
            self.assertIsNone(morning._kids_change(self.SAT))             # away -> away
            self.assertEqual(morning._kids_change(self.WED), "papa")      # away on Tuesday, here on Wednesday
        here = {"family_kids": [all_day("Papa weekend", self.SAT), all_day("Papa weekend", self.SUN)]}
        with Fake(here):
            self.assertIsNone(morning._kids_change(self.FRI))             # here on Thursday and Friday
            self.assertEqual(morning._kids_change(self.NEXT_MON), "mama")  # Sunday here, Monday away

    def test_without_kids_there_is_no_hand_over(self):
        with Fake({}, kids=None):
            self.assertIsNone(morning._kids_change(self.SAT))


class Agenda(unittest.TestCase):
    DAY = date(2026, 11, 4)

    def test_hand_over_markers_are_not_listed_as_appointments(self):
        events = {"family_kids": [all_day("Mama weekend", self.DAY), timed("Zwemles", self.DAY, 16)],
                  "anna": [timed("Tandarts", self.DAY, 9), all_day("Verjaardag", self.DAY)]}
        with Fake(events):
            people = morning._agenda(self.DAY)
        self.assertEqual([p["name"] for p in people], ["Anna", "Kids"])
        self.assertEqual([i["title"] for i in people[0]["items"]], ["Verjaardag", "Tandarts"])   # all-day first
        self.assertEqual(people[1]["items"], [{"title": "Zwemles", "time": "16:00"}])

    def test_bins_come_from_the_bins_calendar_only_when_one_is_configured(self):
        events = {"waste": [all_day("Gfte", self.DAY), all_day("PMD", self.DAY + timedelta(days=1))]}
        with Fake(events):
            self.assertEqual(morning._bins(self.DAY), ["GFT"])
        with Fake(events, bins_calendar=None):
            self.assertEqual(morning._bins(self.DAY), [])

    def test_bags_follow_the_configured_rules(self):
        people = [{"name": "Kids", "items": [{"title": "Zwemles C-diploma", "time": "16:00"}, {"title": "Gym", "time": None}]}]
        with Fake({}, bag_rules=[{"match": "zwem", "label": "Zwemspullen"}]):
            self.assertEqual(morning._bags(self.DAY, people), ["Zwemspullen"])
        with Fake({}):   # the defaults
            self.assertEqual(morning._bags(self.DAY, people), ["Zwemspullen", "Gymspullen"])


class News(unittest.TestCase):
    def test_a_title_between_quotes_loses_them(self):
        self.assertEqual(morning._unquote('"Dit is gezegd"'), "Dit is gezegd")
        self.assertEqual(morning._unquote('"Een" en "twee"'), '"Een" en "twee"')

    def test_long_titles_are_cut_at_a_clause_break(self):
        title = "Het kabinet presenteert nieuwe plannen voor de woningbouw, terwijl de gemeenten nog wachten op meer duidelijkheid"
        short = morning._shorten(title)
        self.assertLessEqual(len(short), morning.NEWS_MAX_CHARS)
        self.assertEqual(short, "Het kabinet presenteert nieuwe plannen voor de woningbouw")


if __name__ == "__main__":
    unittest.main()

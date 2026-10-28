"""Tests for tv.py with a fake art channel. Run: .venv/bin/python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import tv  # noqa: E402

HOST = "10.0.0.5"


class FakeArt:
    """Just enough of the art channel to check what we ask the TV to do."""

    def __init__(self, current="USER_1", artmode="on", slideshow=None):
        self.current, self.artmode = current, artmode
        self.slideshow = slideshow or {"value": "off", "type": "", "category_id": ""}
        self.images, self.uploads, self.calls = {"USER_1", "USER_2"}, 0, []

    def get_current(self):
        return {"content_id": self.current}

    def get_slideshow_status(self):
        return dict(self.slideshow)

    def set_slideshow_status(self, duration=0, type=True, category=2):
        self.calls.append(("slideshow", duration, type, category))
        self.slideshow = {"value": str(duration) if duration > 0 else "off",
                          "type": "shuffleslideshow" if type else "slideshow", "category_id": f"MY-C000{category}"}

    def get_artmode(self):
        return self.artmode

    def set_artmode(self, on):
        self.calls.append(("artmode", on))
        self.artmode = "on" if on else "off"

    def upload(self, data, **kwargs):
        self.uploads += 1
        content_id = f"MY_F{self.uploads:04d}"
        self.images.add(content_id)
        return content_id

    def select_image(self, content_id, category=None, show=True):
        assert content_id in self.images, f"{content_id} does not exist on the TV"
        self.current = content_id

    def delete_list(self, content_ids):
        self.calls.append(("delete", list(content_ids)))
        self.images -= set(content_ids)

    def available(self):
        return [{"content_id": c, "category_id": "M0002", "image_date": "2026:01:01"} for c in sorted(self.images)] * 2


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tv.STATE_DIR = Path(self.tmp.name)


class ShowAndHide(StateTestCase):
    def test_hide_puts_the_previous_art_back_and_removes_our_upload(self):
        art = FakeArt(current="USER_2")
        board = tv.show_image(HOST, b"png", art)
        self.assertEqual(art.current, board)
        result = tv.hide(HOST, art=art)
        self.assertEqual(art.current, "USER_2")
        self.assertNotIn(board, art.images)
        self.assertEqual(result["restored"], "USER_2")
        self.assertEqual(tv.load_state(HOST), {"uploads": [], "previous": None, "showing": False})

    def test_showing_twice_keeps_the_original_art_and_replaces_the_old_upload(self):
        art = FakeArt(current="USER_1")
        first = tv.show_image(HOST, b"a", art)
        second = tv.show_image(HOST, b"b", art)
        self.assertNotIn(first, art.images)          # replaced
        self.assertIn(second, art.images)
        self.assertEqual(tv.load_state(HOST)["previous"]["content_id"], "USER_1")   # not our own first image
        tv.hide(HOST, art=art)
        self.assertEqual(art.current, "USER_1")
        self.assertEqual(art.images, {"USER_1", "USER_2"})   # the user's art is untouched

    def test_a_running_slideshow_is_paused_while_the_dashboard_is_up_and_resumed_afterwards(self):
        art = FakeArt(current="USER_1", slideshow={"value": "15", "type": "shuffleslideshow", "category_id": "MY-C0004"})
        tv.show_image(HOST, b"png", art)
        self.assertEqual(art.slideshow["value"], "off")
        tv.hide(HOST, art=art)
        self.assertEqual(art.slideshow, {"value": "15", "type": "shuffleslideshow", "category_id": "MY-C0004"})

    def test_a_plain_slideshow_resumes_as_plain(self):
        art = FakeArt(slideshow={"value": "5", "type": "slideshow", "category_id": "MY-C0002"})
        tv.show_image(HOST, b"png", art)
        tv.hide(HOST, art=art)
        self.assertEqual(art.slideshow, {"value": "5", "type": "slideshow", "category_id": "MY-C0002"})

    def test_the_tv_goes_back_to_tv_mode_when_it_was_in_tv_mode_before(self):
        art = FakeArt(artmode="off")
        tv.show_image(HOST, b"png", art)
        self.assertEqual(art.artmode, "on")          # the dashboard needs art mode
        tv.hide(HOST, art=art)
        self.assertEqual(art.artmode, "off")

    def test_after_youtube_the_tv_returns_to_art_mode(self):
        art = FakeArt()
        tv.show_image(HOST, b"png", art)
        art.artmode = "off"                           # YouTube took the TV out of art mode
        tv.hide(HOST, art=art)
        self.assertEqual(art.artmode, "on")

    def test_hide_without_a_dashboard_does_not_delete_anything(self):
        art = FakeArt()
        tv.hide(HOST, art=art)
        self.assertEqual(art.images, {"USER_1", "USER_2"})
        self.assertEqual(art.current, "USER_1")


class UnknownPrevious(StateTestCase):
    def test_state_from_an_older_version_has_no_previous_art(self):
        (tv.STATE_DIR / f"uploads-{HOST}.json").write_text(json.dumps(["MY_F0001"]))
        art = FakeArt(current="MY_F0001")
        art.images.add("MY_F0001")
        state = tv.load_state(HOST)
        self.assertTrue(state["showing"])
        self.assertIsNone(state["previous"])
        # without a fallback our image is only removed
        result = tv.hide(HOST, art=art)
        self.assertIsNone(result["restored"])
        self.assertNotIn("MY_F0001", art.images)

    def test_the_fallback_is_used_when_nothing_was_recorded(self):
        (tv.STATE_DIR / f"uploads-{HOST}.json").write_text(json.dumps(["MY_F0001"]))
        art = FakeArt(current="MY_F0001")
        art.images.add("MY_F0001")
        result = tv.hide(HOST, fallback="USER_2", art=art)
        self.assertEqual(art.current, "USER_2")
        self.assertEqual(result["restored"], "USER_2")

    def test_our_own_image_is_never_used_as_the_fallback(self):
        (tv.STATE_DIR / f"uploads-{HOST}.json").write_text(json.dumps(["MY_F0001"]))
        art = FakeArt(current="MY_F0001")
        art.images.add("MY_F0001")
        result = tv.hide(HOST, fallback="MY_F0001", art=art)
        self.assertIsNone(result["restored"])

    def test_only_our_own_uploads_are_ever_listed_as_ours(self):
        art = FakeArt()
        board = tv.show_image(HOST, b"png", art)
        listing = {item["content_id"]: item["ours"] for item in tv.list_art(HOST, art)}
        self.assertEqual(listing, {"USER_1": False, "USER_2": False, board: True})


class YouTubeIds(unittest.TestCase):
    def test_accepted_forms(self):
        vid = "dQw4w9WgXcQ"
        for value in (vid, f"https://www.youtube.com/watch?v={vid}", f"https://youtube.com/watch?v={vid}&t=42s",
                      f"https://m.youtube.com/watch?v={vid}", f"https://youtu.be/{vid}", f"https://youtu.be/{vid}?si=abc",
                      f"https://www.youtube.com/shorts/{vid}", f"https://www.youtube.com/embed/{vid}",
                      f"https://www.youtube.com/live/{vid}", f"www.youtube.com/watch?v={vid}", f"  {vid}  "):
            self.assertEqual(tv.parse_youtube_id(value), vid, value)

    def test_rejected_forms(self):
        for value in ("", "https://example.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=short",
                      "https://www.youtube.com/@somechannel", "dQw4w9WgXc", "dQw4w9WgXcQ&extra=1",
                      "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ"):
            with self.assertRaises(ValueError, msg=value):
                tv.parse_youtube_id(value)


class FakeYouTubeTV:
    """The TV's REST API (port 8001) and DIAL service (port 8080), as far as the YouTube flow uses them.

    Like the real TV: a fresh start is visible, DIAL says "running" at once and publishes the screen id after a
    while; closing the app removes the id. `background=True` is the app after the TV left it: running but not
    visible, DIAL "stopped", and still publishing the id of the previous session.
    """

    def __init__(self, running=False, polls_until_ready=3, background=False):
        self.running, self.visible, self.ready_after, self.polls, self.log = running, not background, polls_until_ready, 0, []
        self.stale = background

    def http(self, host, port, path, method="GET", body=None):
        self.log.append((port, method))
        if port == tv.REST_PORT:
            if method == "POST":
                self.running, self.visible, self.stale, self.polls = True, True, False, 0
                return 200, b"HTTP/"
            if method == "DELETE":
                self.running, self.visible, self.stale = False, False, False
                return 200, b"true\n"
            return 200, json.dumps({"running": self.running, "visible": self.visible}).encode()
        self.polls += 1
        state = "running" if self.running and self.visible else "stopped"
        if self.stale:
            screen = "OLD"
        else:
            screen = "SCR" if self.running and self.polls >= self.ready_after else None
        extra = f"<screenId>{screen}</screenId>" if screen else ""
        return 200, f"<service><state>{state}</state><additionalData>{extra}</additionalData></service>".encode()


class FakeSession:
    played = []

    def __init__(self, screen_id):
        self.screen_id = screen_id

    def play_video(self, video_id, start_time="0"):
        FakeSession.played.append((self.screen_id, video_id, start_time))


class YouTubeFlow(unittest.TestCase):
    def setUp(self):
        FakeSession.played = []

    def start(self, fake, **kwargs):
        with unittest.mock.patch.object(tv, "_http", fake.http):
            return tv.youtube_start(HOST, "https://youtu.be/dQw4w9WgXcQ", session_factory=FakeSession, sleep=lambda s: None, **kwargs)

    def test_starts_the_app_waits_for_the_screen_id_and_plays_the_video(self):
        fake = FakeYouTubeTV(running=False, polls_until_ready=4)
        self.assertEqual(self.start(fake, start=30), "dQw4w9WgXcQ")
        self.assertEqual(FakeSession.played, [("SCR", "dQw4w9WgXcQ", "30")])
        self.assertEqual(fake.log.count((tv.REST_PORT, "POST")), 1)

    def test_an_app_that_is_already_running_is_not_started_again(self):
        fake = FakeYouTubeTV(running=True, polls_until_ready=1)
        self.start(fake)
        self.assertNotIn((tv.REST_PORT, "POST"), fake.log)
        self.assertEqual(len(FakeSession.played), 1)

    def test_an_app_that_lingers_in_the_background_is_restarted_not_trusted(self):
        # The TV left the app: REST says running but not visible, DIAL says stopped yet still shows the old screen id.
        # A video sent there is accepted by YouTube and never played.
        fake = FakeYouTubeTV(running=True, polls_until_ready=3, background=True)
        self.start(fake)
        self.assertEqual(FakeSession.played, [("SCR", "dQw4w9WgXcQ", "0")])      # the new session, never "OLD"
        rest = [m for port, m in fake.log if port == tv.REST_PORT and m != "GET"]
        self.assertEqual(rest, ["DELETE", "POST"])

    def test_a_stale_screen_id_is_not_used_while_dial_says_stopped(self):
        fake = FakeYouTubeTV(running=True, background=True)
        with unittest.mock.patch.object(tv, "_http", fake.http):
            self.assertIsNone(tv._screen_id(HOST))

    def test_an_app_that_never_gets_ready_is_reported(self):
        fake = FakeYouTubeTV(running=False, polls_until_ready=10**9)
        with unittest.mock.patch.object(tv, "_http", fake.http), self.assertRaisesRegex(tv.TVError, "did not become ready"):
            tv.youtube_start(HOST, "dQw4w9WgXcQ", wait=0.05, session_factory=FakeSession, sleep=lambda s: None)
        self.assertEqual(FakeSession.played, [])

    def test_a_video_that_youtube_refuses_is_reported(self):
        class Refusing(FakeSession):
            def play_video(self, *a, **k):
                raise RuntimeError("401 lounge")
        fake = FakeYouTubeTV(running=True, polls_until_ready=1)
        with unittest.mock.patch.object(tv, "_http", fake.http), self.assertRaisesRegex(tv.TVError, "did not accept"):
            tv.youtube_start(HOST, "dQw4w9WgXcQ", session_factory=Refusing, sleep=lambda s: None)

    def test_a_bad_link_never_reaches_the_tv(self):
        fake = FakeYouTubeTV()
        with unittest.mock.patch.object(tv, "_http", fake.http), self.assertRaises(ValueError):
            tv.youtube_start(HOST, "https://example.com/x", session_factory=FakeSession)
        self.assertEqual(fake.log, [])

    def test_stop_closes_a_running_app_and_ignores_a_stopped_one(self):
        fake = FakeYouTubeTV(running=True)
        with unittest.mock.patch.object(tv, "_http", fake.http):
            self.assertTrue(tv.youtube_stop(HOST))
            self.assertFalse(fake.running)
            self.assertFalse(tv.youtube_stop(HOST))


if __name__ == "__main__":
    unittest.main()

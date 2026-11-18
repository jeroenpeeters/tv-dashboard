"""Tests for the live page (our Tizen kiosk app) in tv.py against a fake TV: REST for the app, a channel for the page."""
import base64
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import tv  # noqa: E402
from test_tv import FakeArt, StateTestCase  # noqa: E402

HOST = "10.0.0.5"
PAGE = "https://recipes.example.com/tv?key=SECRET"
APP = f"/api/v2/applications/{tv.KIOSK_APP_ID}"


def client(name: bytes) -> dict:
    return {"attributes": {"name": base64.b64encode(name).decode()}, "id": "x", "isHost": True}


class FakeKioskTV:
    """The app's REST status plus the channel it listens on. The app joins the channel after it was started."""

    def __init__(self, installed=True, running=False, app_comes_up=True):
        self.installed, self.running, self.app_comes_up = installed, running, app_comes_up
        self.log, self.sent = [], []

    def http(self, host, port, path, method="GET", body=None):
        self.log.append((method, path))
        if path != APP or not self.installed:
            return 404, b""
        if method == "POST":
            self.running = True
            return 200, b"true"
        if method == "DELETE":
            self.running = False
            return 200, b"true"
        return 200, json.dumps({"id": tv.KIOSK_APP_ID, "running": self.running, "visible": self.running}).encode()

    def ws(self, url, timeout=3):
        self.log.append(("WS", url.split("?")[0]))
        if not (self.running and self.app_comes_up):
            raise ConnectionRefusedError("the channel is not there yet")
        fake = self

        class Socket:
            queue = [json.dumps({"event": "ms.channel.connect", "data": {"clients": [client(b"tvdash-service"), client(b"tvdash-app")]}})]

            def recv(self):
                if not self.queue:
                    raise TimeoutError("nothing")
                return self.queue.pop(0)

            def send(self, text):
                fake.sent.append(json.loads(text))

            def close(self):
                pass

        return Socket()


class KioskShow(StateTestCase):
    def show(self, fake, url=PAGE, art=None, **kwargs):
        art = art or FakeArt()
        with unittest.mock.patch.object(tv, "_http", fake.http):
            return tv.kiosk_show(HOST, url, art=art, connect_ws=fake.ws, sleep=lambda s: None, **kwargs), art

    def test_the_app_is_started_and_then_told_which_page_to_open(self):
        fake = FakeKioskTV()
        host, _ = self.show(fake)
        self.assertEqual(host, "recipes.example.com")          # the key in the query is never returned
        self.assertEqual(fake.sent, [{"method": "ms.channel.emit",
                                      "params": {"event": "tvdash.show", "to": "broadcast", "data": {"url": PAGE}}}])
        self.assertIn(("POST", APP), fake.log)

    def test_a_running_app_is_restarted_because_it_is_stuck_inside_its_last_page(self):
        fake = FakeKioskTV(running=True)
        self.show(fake)
        methods = [m for m, p in fake.log if p == APP and m != "GET"]
        self.assertEqual(methods, ["DELETE", "POST"])

    def test_the_art_to_go_back_to_is_remembered_like_for_a_picture(self):
        art = FakeArt(current="USER_2")
        self.show(FakeKioskTV(), art=art)
        state = tv.load_state(HOST)
        self.assertTrue(state["showing"])
        self.assertEqual(state["previous"]["content_id"], "USER_2")

    def test_an_app_that_is_not_installed_is_explained(self):
        with self.assertRaisesRegex(tv.TVError, "not installed"):
            self.show(FakeKioskTV(installed=False))

    def test_pages_that_are_not_plain_http_never_reach_the_tv(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https://user:pw@recipes.example.com/", "recipes.example.com/tv"):
            fake = FakeKioskTV()
            with self.assertRaises(ValueError, msg=url):
                self.show(fake, url=url)
            self.assertEqual(fake.log, [], url)

    def test_an_app_that_never_joins_the_channel_is_reported(self):
        with self.assertRaisesRegex(tv.TVError, "did not come up"):
            self.show(FakeKioskTV(app_comes_up=False), wait=0.05)


class KioskStop(StateTestCase):
    def test_stop_closes_a_running_app_and_ignores_a_stopped_one(self):
        fake = FakeKioskTV(running=True)
        with unittest.mock.patch.object(tv, "_http", fake.http):
            self.assertTrue(tv.kiosk_stop(HOST))
            self.assertFalse(tv.kiosk_stop(HOST))

    def test_state_names_the_three_cases(self):
        for fake, expected in ((FakeKioskTV(running=True), "running"), (FakeKioskTV(running=False), "stopped"), (FakeKioskTV(installed=False), "not installed")):
            with unittest.mock.patch.object(tv, "_http", fake.http):
                self.assertEqual(tv.kiosk_state(HOST), expected)

    def test_stop_without_the_app_installed_is_quiet(self):
        with unittest.mock.patch.object(tv, "_http", FakeKioskTV(installed=False).http):
            self.assertFalse(tv.kiosk_stop(HOST))

    def test_hide_closes_the_live_page_with_the_rest_of_the_dashboard(self):
        art = FakeArt(current="USER_2")
        fake = FakeKioskTV()
        with unittest.mock.patch.object(tv, "_http", fake.http):
            tv.kiosk_show(HOST, PAGE, art=art, connect_ws=fake.ws, sleep=lambda s: None)
            with unittest.mock.patch.object(tv, "wake", return_value=False), unittest.mock.patch.object(tv, "connect", return_value=art):
                result = tv.hide(HOST)
        self.assertFalse(fake.running)
        self.assertEqual(result["restored"], "USER_2")
        self.assertFalse(tv.load_state(HOST)["showing"])


if __name__ == "__main__":
    unittest.main()

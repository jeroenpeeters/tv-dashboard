"""Tests for server.py with the TV and the renderer replaced by fakes."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.update(API_KEY="secret", TVS="keuken=10.0.0.5,woonkamer=10.0.0.6", DOORBELL_CAMERA="camera.bel",
                  HASS_URL="http://ha:8123", HASS_TOKEN="x", FALLBACK_ART="USER_9",
                  TVDASH_CONFIG=str(Path(__file__).parent.parent / "config.example.json"))   # also checked at import
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import tv  # noqa: E402
import tvdash  # noqa: E402

KEY = {"X-Api-Key": "secret"}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, value in ((tv, "STATE_DIR"), (tvdash, "OUT_DIR")):
            patcher = mock.patch.object(target, value, Path(tmp.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        server._last.clear()

    def flush(self):
        server._executor.submit(lambda: None).result()   # wait for background jobs

    # --- access
    def test_health_needs_no_key(self):
        self.assertEqual(self.client.get("/health").json(), {"ok": True})

    def test_everything_else_needs_the_key(self):
        for method, path in (("get", "/status"), ("get", "/art"), ("post", "/show"), ("post", "/hide"), ("post", "/youtube")):
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)
            self.assertEqual(getattr(self.client, method)(path, headers={"X-Api-Key": "wrong"}).status_code, 401, path)

    def test_unknown_tv_is_rejected_so_no_other_address_can_be_reached(self):
        self.assertEqual(self.client.post("/hide", json={"tv": "10.9.9.9"}, headers=KEY).status_code, 404)
        self.assertEqual(self.client.post("/youtube", json={"tv": "evil", "video": "dQw4w9WgXcQ"}, headers=KEY).status_code, 404)

    # --- show
    def test_show_morning_answers_at_once_and_does_the_work_in_the_background(self):
        with mock.patch.object(tvdash, "make_morning", side_effect=lambda out, day=None, print_data=False: out.write_bytes(b"png")) as make, \
                mock.patch.object(tv, "show_image", return_value="MY_F0001") as show:
            response = self.client.post("/show", json={"dashboard": "morning", "date": "2026-10-28"}, headers=KEY)
            self.assertEqual(response.status_code, 202)
            self.flush()
        self.assertEqual(str(make.call_args.args[1]), "2026-10-28")
        show.assert_called_once_with("10.0.0.5", b"png")        # the first TV is the default
        self.assertTrue(server._last["keuken"]["ok"])

    def test_show_can_wait_and_picks_the_tv_by_name(self):
        with mock.patch.object(tvdash, "make_morning", side_effect=lambda out, day=None, print_data=False: out.write_bytes(b"png")), \
                mock.patch.object(tv, "show_image", return_value="MY_F0007") as show:
            response = self.client.post("/show", json={"tv": "woonkamer", "wait": True}, headers=KEY)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["detail"], {"image": "MY_F0007", "dashboard": "morning"})
        self.assertEqual(show.call_args.args[0], "10.0.0.6")

    def test_a_failing_job_is_reported_not_swallowed(self):
        with mock.patch.object(tvdash, "make_morning", side_effect=lambda *a, **k: Path(a[0]).write_bytes(b"png")), \
                mock.patch.object(tv, "show_image", side_effect=tv.TVError("10.0.0.5: not reachable")):
            response = self.client.post("/show", json={"wait": True}, headers=KEY)
        self.assertEqual(response.status_code, 502)
        self.assertIn("not reachable", response.json()["error"])
        self.assertFalse(server._last["keuken"]["ok"])

    def test_doorbell_uses_the_configured_camera(self):
        with mock.patch.object(tvdash, "make_doorbell", side_effect=lambda out, cam, url: out.write_bytes(b"png")) as make, \
                mock.patch.object(tv, "show_image", return_value="MY_F0002"):
            self.client.post("/show", json={"dashboard": "doorbell", "wait": True}, headers=KEY)
        self.assertEqual(make.call_args.args[1:], ("camera.bel", "http://ha:8123"))

    def test_camera_names_are_validated(self):
        response = self.client.post("/show", json={"dashboard": "doorbell", "camera": "light.x; rm -rf /"}, headers=KEY)
        self.assertEqual(response.status_code, 422)

    def test_morning_needs_home_assistant_to_be_configured(self):
        with mock.patch.dict(os.environ, {"HASS_TOKEN": ""}):
            self.assertEqual(self.client.post("/show", json={}, headers=KEY).status_code, 503)

    def test_morning_needs_the_household_settings(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("TVDASH_CONFIG")
            self.assertEqual(self.client.post("/show", json={}, headers=KEY).status_code, 503)
            with mock.patch.object(tvdash, "make_doorbell", side_effect=lambda out, cam, url: out.write_bytes(b"png")), \
                    mock.patch.object(tv, "show_image", return_value="MY_F0003"):
                response = self.client.post("/show", json={"dashboard": "doorbell", "wait": True}, headers=KEY)
            self.assertEqual(response.status_code, 200)   # the doorbell does not need them

    # --- hide
    def test_hide_uses_the_default_fallback_unless_one_is_given(self):
        with mock.patch.object(tv, "hide", return_value={"restored": "USER_9"}) as hide:
            self.client.post("/hide", json={"wait": True}, headers=KEY)
            self.assertEqual(hide.call_args.kwargs["fallback"], "USER_9")
            self.client.post("/hide", json={"wait": True, "fallback": "USER_2"}, headers=KEY)
            self.assertEqual(hide.call_args.kwargs["fallback"], "USER_2")

    # --- youtube
    def test_youtube_runs_in_the_background_and_can_be_waited_for(self):
        with mock.patch.object(tv, "youtube_start", return_value="dQw4w9WgXcQ") as start:
            response = self.client.post("/youtube", json={"video": "https://youtu.be/dQw4w9WgXcQ", "start": 30}, headers=KEY)
            self.assertEqual(response.status_code, 202)
            self.flush()
            start.assert_called_once_with("10.0.0.5", "https://youtu.be/dQw4w9WgXcQ", 30)
            response = self.client.post("/youtube", json={"video": "dQw4w9WgXcQ", "wait": True}, headers=KEY)
            self.assertEqual(response.json()["detail"], {"started": "dQw4w9WgXcQ"})

    def test_a_bad_link_is_refused_before_anything_is_queued(self):
        with mock.patch.object(tv, "youtube_start") as start:
            response = self.client.post("/youtube", json={"video": "https://example.com/nothing-here"}, headers=KEY)
            self.flush()
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()

    def test_a_tv_that_refuses_youtube_is_reported(self):
        with mock.patch.object(tv, "youtube_start", side_effect=tv.TVError("refused")):
            response = self.client.post("/youtube", json={"video": "dQw4w9WgXcQ", "wait": True}, headers=KEY)
        self.assertEqual(response.status_code, 502)
        self.assertIn("refused", response.json()["error"])

    def test_youtube_stop(self):
        with mock.patch.object(tv, "youtube_stop", return_value=False):
            response = self.client.post("/youtube/stop", json={"wait": True}, headers=KEY)
        self.assertEqual(response.json()["detail"], {"stopped": False})

    # --- status
    def test_status_lists_each_tv(self):
        with mock.patch.object(tv, "youtube_state", return_value="stopped"):
            data = self.client.get("/status", headers=KEY).json()
        self.assertEqual(set(data), {"keuken", "woonkamer"})
        self.assertFalse(data["keuken"]["dashboard_showing"])
        self.assertEqual(data["keuken"]["youtube"], "stopped")


if __name__ == "__main__":
    unittest.main()

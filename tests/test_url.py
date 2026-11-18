"""Tests for the "show a web page" helpers in tvdash.py (no browser, no network)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
import tvdash  # noqa: E402

HOSTS = {"recipes.example.com"}


class CheckUrl(unittest.TestCase):
    def test_a_page_on_a_listed_host_is_accepted_in_any_case(self):
        self.assertEqual(tvdash.check_url(" HTTPS://Recipes.Example.com:8443/tv?key=x ", HOSTS), "HTTPS://Recipes.Example.com:8443/tv?key=x")

    def test_other_hosts_are_forbidden(self):
        for url in ("https://example.com/", "https://recipes.example.com.evil.org/", "http://127.0.0.1:8080/", "http://[::1]/"):
            with self.assertRaises(PermissionError, msg=url):
                tvdash.check_url(url, HOSTS)

    def test_odd_urls_are_rejected(self):
        for url in ("", "file:///etc/passwd", "javascript:alert(1)", "https://a:b@recipes.example.com/", "//recipes.example.com/x"):
            with self.assertRaises(ValueError, msg=url):
                tvdash.check_url(url, HOSTS)

    def test_a_host_hidden_behind_credentials_is_not_trusted(self):
        with self.assertRaises(ValueError):
            tvdash.check_url("https://recipes.example.com@evil.org/", HOSTS)


    def test_without_a_list_any_host_passes_but_the_address_is_still_checked(self):
        self.assertEqual(tvdash.check_url("http://192.168.1.20:8123/x", None), "http://192.168.1.20:8123/x")
        for url in ("file:///etc/passwd", "https://a:b@example.com/", "ftp://example.com/"):
            with self.assertRaises(ValueError, msg=url):
                tvdash.check_url(url, None)


class Redact(unittest.TestCase):
    def test_the_query_and_credentials_never_survive(self):
        self.assertEqual(tvdash.redact_url("https://recipes.example.com/tv?key=SECRET#frag"), "https://recipes.example.com/tv?…")
        self.assertEqual(tvdash.redact_url("https://user:pw@recipes.example.com/tv"), "https://recipes.example.com/tv")

    def test_a_failed_render_reports_no_key(self):
        url = "https://recipes.example.com/tv?key=SECRET"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tvdash, "render", side_effect=RuntimeError(f"goto {url} failed")):
            with self.assertRaises(RuntimeError) as caught:
                tvdash.make_url(Path(tmp) / "x.png", url, HOSTS)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)   # the original message (with the key) is not chained either

    def test_a_forbidden_page_never_reaches_the_browser(self):
        with mock.patch.object(tvdash, "render") as render, self.assertRaises(PermissionError):
            tvdash.make_url(Path("x.png"), "https://evil.example.org/", HOSTS)
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_MODULE_PATH = Path(__file__).resolve().parent.parent / "helpers" / "update_check.py"
_spec = importlib.util.spec_from_file_location("update_check", _MODULE_PATH)
uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uc)

DAY = uc.CHECK_INTERVAL_SECONDS
URL = "https://api.example.test/releases/latest"


class FakeSession:
    def __init__(self, tag="v1.3.0", offline=False, delay=0):
        self.tag = tag
        self.offline = offline
        self.delay = delay
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        time.sleep(self.delay)
        if self.offline:
            raise OSError("offline")
        return mock.Mock(
            status_code=200,
            json=lambda: {"tag_name": self.tag, "html_url": "https://example.test"},
        )


class IsNewerTests(unittest.TestCase):
    def test_compares_numerically(self):
        self.assertTrue(uc.is_newer("v1.2.10", "1.2.9"))
        self.assertFalse(uc.is_newer("1.2.1", "1.2.1"))
        self.assertFalse(uc.is_newer("1.3.0-rc1", "1.2.1"))


class CheckForUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.ini"
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(uc.URL_OVERRIDE_ENV, None)

    def check(self, session, now=DAY):
        return uc.check_for_update(self.config_path, "1.2.1", session, now, URL)

    def test_newer_release_is_reported(self):
        release = self.check(FakeSession("v1.3.0"))
        self.assertEqual(release, uc.Release("1.3.0", "https://example.test"))

    def test_up_to_date_reports_nothing(self):
        self.assertIsNone(self.check(FakeSession("v1.2.1")))

    def test_offline_reports_nothing(self):
        self.assertIsNone(self.check(FakeSession(offline=True)))

    def test_slow_connection_gives_up_at_the_deadline(self):
        with mock.patch.object(uc, "REQUEST_TIMEOUT_SECONDS", 0.1):
            start = time.monotonic()
            self.assertIsNone(self.check(FakeSession(delay=2)))
            self.assertLess(time.monotonic() - start, 1)

    def test_checks_at_most_daily_and_serves_cache(self):
        session = FakeSession("v1.3.0")
        self.check(session, now=DAY)
        cached = self.check(FakeSession(offline=True), now=DAY + 60)
        self.assertEqual(cached.version, "1.3.0")
        self.check(session, now=2 * DAY)
        self.assertEqual(session.calls, 2)

    def test_skipped_version_hidden_until_a_newer_one(self):
        self.check(FakeSession("v1.3.0"))
        uc.skip_version(self.config_path, "1.3.0")
        self.assertIsNone(self.check(FakeSession("v1.3.0"), now=2 * DAY))
        self.assertIsNotNone(self.check(FakeSession("v1.4.0"), now=3 * DAY))

    def test_keeps_api_key_in_config(self):
        self.config_path.write_text("[API]\nkey = secret\n")
        self.check(FakeSession())
        self.assertIn("key = secret", self.config_path.read_text())


if __name__ == "__main__":
    unittest.main()

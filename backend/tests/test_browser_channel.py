"""Which browser opens, and whether the app admits when it is not the chosen one.

Chrome and Edge share one profile directory and each stamps it with its own
version on exit. Edge ships ahead of Chrome, so once Edge has run the directory
looks like a downgrade: Chrome moves the too-new data aside and exits during that
first launch, and the next launch works.

The launcher used to give up after one failure and start Edge instead. Edge then
re-stamped the directory with the newer version, recreating the condition - so a
profile touched by Edge once could never open in Chrome again, and nothing
anywhere said the setting was not being honoured. These tests hold both halves:
the chosen browser gets its second chance, and a substitute is always reported.
"""
import unittest
from unittest import mock

from app.services import browser_session


class FakeChromium:
    """Records launch attempts and fails the ones it is told to."""

    def __init__(self, failures):
        self.failures = list(failures)
        self.attempts: list[str] = []

    def launch_persistent_context(self, **options):
        channel = options.get("channel")
        self.attempts.append(channel or "chromium")
        if self.failures:
            problem = self.failures.pop(0)
            if problem is not None:
                raise RuntimeError(problem)
        return mock.MagicMock(pages=[], on=mock.Mock())


DOWNGRADE = (
    "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
    "Browser logs:\n"
    "[err] chrome\\browser\\downgrade\\downgrade_utils.cc:36 moving profile aside\n"
)


class ChannelChoiceTests(unittest.TestCase):
    def launch(self, failures, channel="chrome"):
        worker = browser_session._BrowserWorker()
        chromium = FakeChromium(failures)
        worker._playwright = mock.Mock(chromium=chromium)
        worker._context_closed = True
        with mock.patch.object(browser_session.settings, "load",
                               return_value={"browser_channel": channel}), \
             mock.patch.object(browser_session.settings, "browser_profile_path",
                               return_value="/tmp/profile"):
            worker.context()
        return worker, chromium

    def test_the_chosen_browser_is_tried_again_before_any_substitute(self):
        """The bug: one downgrade failure handed the session to Edge."""
        worker, chromium = self.launch([DOWNGRADE, None])
        self.assertEqual(chromium.attempts, ["chrome", "chrome"])
        self.assertEqual(worker._channel_used, "chrome")

    def test_a_second_chance_is_all_the_downgrade_needs(self):
        worker, _ = self.launch([DOWNGRADE, None])
        self.assertFalse(browser_session.channel_status.__doc__ is None)
        self.assertEqual(worker._channel_note, "")

    def test_edge_is_used_only_after_the_choice_fails_twice(self):
        worker, chromium = self.launch([DOWNGRADE, DOWNGRADE, None])
        self.assertEqual(chromium.attempts, ["chrome", "chrome", "msedge"])
        self.assertEqual(worker._channel_used, "msedge")

    def test_a_substitute_is_never_silent(self):
        worker, _ = self.launch([DOWNGRADE, DOWNGRADE, None])
        self.assertIn("chrome could not start", worker._channel_note)
        self.assertIn("msedge was used instead", worker._channel_note)
        self.assertIn("Target page, context or browser has been closed",
                      worker._channel_note)

    def test_the_note_carries_one_readable_line_not_the_whole_log(self):
        worker, _ = self.launch([DOWNGRADE, DOWNGRADE, None])
        self.assertNotIn("downgrade_utils", worker._channel_note)
        self.assertNotIn("\n", worker._channel_note)

    def test_a_locked_profile_is_reported_rather_than_worked_around(self):
        """Another window holding the profile is not a reason to switch browser."""
        worker = browser_session._BrowserWorker()
        chromium = FakeChromium(["Failed: existing browser session"] * 4)
        worker._playwright = mock.Mock(chromium=chromium)
        worker._context_closed = True
        with mock.patch.object(browser_session.settings, "load",
                               return_value={"browser_channel": "chrome"}), \
             mock.patch.object(browser_session.settings, "browser_profile_path",
                               return_value="/tmp/profile"):
            with self.assertRaises(RuntimeError) as caught:
                worker.context()
        self.assertIn("already open", str(caught.exception))
        self.assertEqual(chromium.attempts, ["chrome"])

    def test_chromium_is_honoured_as_chosen_and_never_substituted(self):
        worker, chromium = self.launch([DOWNGRADE, None], channel="chromium")
        self.assertEqual(chromium.attempts, ["chromium", "chromium"])
        self.assertEqual(worker._channel_used, "chromium")
        self.assertEqual(worker._channel_note, "")

    def test_each_supported_choice_is_tried_before_anything_else(self):
        for choice in ("chrome", "msedge"):
            with self.subTest(choice=choice):
                _, chromium = self.launch([DOWNGRADE, None], channel=choice)
                self.assertEqual(chromium.attempts[:2], [choice, choice])


class ChannelStatusTests(unittest.TestCase):
    """The status the API reports - previously `channel_in_use` was never called."""

    def setUp(self):
        self.addCleanup(setattr, browser_session._worker, "_channel_used",
                        browser_session._worker._channel_used)
        self.addCleanup(setattr, browser_session._worker, "_channel_wanted",
                        browser_session._worker._channel_wanted)
        self.addCleanup(setattr, browser_session._worker, "_channel_note",
                        browser_session._worker._channel_note)

    def test_a_substitution_is_visible_in_the_status(self):
        browser_session._worker._channel_wanted = "chrome"
        browser_session._worker._channel_used = "msedge"
        browser_session._worker._channel_note = "chrome could not start"
        status = browser_session.channel_status()
        self.assertTrue(status["browser_substituted"])
        self.assertEqual(status["browser_in_use"], "msedge")
        self.assertEqual(status["browser_wanted"], "chrome")

    def test_no_substitution_is_reported_when_the_choice_was_honoured(self):
        browser_session._worker._channel_wanted = "chrome"
        browser_session._worker._channel_used = "chrome"
        browser_session._worker._channel_note = ""
        self.assertFalse(browser_session.channel_status()["browser_substituted"])

    def test_a_browser_that_never_launched_is_not_called_a_substitution(self):
        browser_session._worker._channel_wanted = ""
        browser_session._worker._channel_used = ""
        browser_session._worker._channel_note = ""
        with mock.patch.object(browser_session.settings, "load",
                               return_value={"browser_channel": "chrome"}):
            status = browser_session.channel_status()
        self.assertFalse(status["browser_substituted"])
        self.assertEqual(status["browser_wanted"], "chrome")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

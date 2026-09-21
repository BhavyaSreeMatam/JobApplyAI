"""Page binding, cancellation and one-at-a-time, tested against a real browser.

These guard three ways a fill could act on something other than the form the
applicant is looking at:

  the wrong tab      taking the newest page in the whole browser meant a second
                     application, a "share this job" popup, or a leftover tab
                     could receive the answers
  after a timeout    abandoning the future left the browser thread typing into a
                     page nobody was waiting for any more
  two at once        a second fill interleaving with the first produces a form
                     neither of them would have written

The browser here is a local headless Chromium with local fixture pages. Nothing
in this file touches an employer site or a saved session.
"""
import unittest

from app.services import autofill, browser_session


class FakeWorker:
    """Just enough of the browser worker for the page-selection logic."""

    def __init__(self, context):
        self._context = context

    def context(self):
        return self._context


class PageSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("playwright is not installed")
        cls._playwright = sync_playwright().start()
        try:
            cls.browser = cls._playwright.chromium.launch(headless=True)
        except Exception as error:  # pragma: no cover - no browser binary
            cls._playwright.stop()
            raise unittest.SkipTest(f"no browser available: {error}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.worker = FakeWorker(self.context)

    def page_on(self, host: str, body: str = "<p>hello</p>"):
        page = self.context.new_page()
        page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html", body=body
        ))
        page.goto(f"https://{host}/apply")
        return page

    def test_the_bound_page_wins_over_the_newest_tab(self):
        """A popup or a second application must not receive these answers."""
        wanted = self.page_on("employer.example")
        browser_session.bind_page("app-1", wanted)
        self.addCleanup(browser_session._PAGES.pop, "app-1", None)
        self.page_on("share.example")           # newer tab, wrong application

        chosen = autofill._active_page(self.worker, "employer.example", "app-1")
        self.assertIs(chosen, wanted)

    def test_a_tab_on_the_employer_host_is_chosen_over_an_unrelated_one(self):
        self.page_on("mail.google.com")
        wanted = self.page_on("employer.example")
        self.page_on("news.example")
        chosen = autofill._active_page(self.worker, "employer.example", "")
        self.assertIs(chosen, wanted)

    def test_no_tab_on_the_expected_host_pauses_rather_than_guessing(self):
        self.page_on("unrelated.example")
        with self.assertRaises(autofill.AmbiguousPage) as caught:
            autofill._active_page(self.worker, "employer.example", "")
        self.assertIn("employer.example", str(caught.exception))

    def test_a_closed_bound_page_is_forgotten_not_used(self):
        page = self.page_on("employer.example")
        browser_session.bind_page("app-2", page)
        self.addCleanup(browser_session._PAGES.pop, "app-2", None)
        page.close()
        self.assertIsNone(browser_session.bound_page("app-2"))

    def test_a_search_engine_tab_is_never_treated_as_an_application(self):
        self.page_on("employer.example")
        self.page_on("www.google.com")          # newest, and never the form
        chosen = autofill._active_page(self.worker, "", "")
        self.assertIn("employer.example", chosen.url)


class CancellationTests(unittest.TestCase):
    def tearDown(self):
        browser_session._ACTIVE.clear()
        browser_session._CANCELLED.clear()

    def test_cancelling_stops_the_next_action(self):
        with browser_session.exclusive("app-3", "Fill this page"):
            self.assertTrue(browser_session.cancel("app-3"))
            with self.assertRaises(browser_session.Cancelled):
                browser_session.check_cancelled("app-3")

    def test_nothing_to_cancel_is_reported_honestly(self):
        self.assertFalse(browser_session.cancel("app-not-running"))

    def test_an_unrelated_application_is_unaffected(self):
        with browser_session.exclusive("app-4", "Fill"):
            with browser_session.exclusive("app-5", "Fill"):
                browser_session.cancel("app-4")
                browser_session.check_cancelled("app-5")   # must not raise
                with self.assertRaises(browser_session.Cancelled):
                    browser_session.check_cancelled("app-4")

    def test_a_second_operation_on_one_application_is_refused(self):
        """Two fills interleaving produce a form neither would have written."""
        with browser_session.exclusive("app-6", "Open and fill"):
            with self.assertRaises(browser_session.Busy) as caught:
                with browser_session.exclusive("app-6", "Fill this page"):
                    pass
            self.assertIn("Open and fill", str(caught.exception))

    def test_the_lock_is_released_even_when_the_work_fails(self):
        with self.assertRaises(ValueError):
            with browser_session.exclusive("app-7", "Fill"):
                raise ValueError("boom")
        with browser_session.exclusive("app-7", "Fill"):
            pass   # must be free again

    def test_cancellation_is_cleared_between_runs(self):
        with browser_session.exclusive("app-8", "Fill"):
            browser_session.cancel("app-8")
        with browser_session.exclusive("app-8", "Fill"):
            browser_session.check_cancelled("app-8")   # must not raise


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Capturing, keeping and recovering a posting's description.

Every Indeed job in the real database arrived with no description. The cause was
not the model, the schema or the word count - it was that a sponsored listing
card links to `/pagead/clk?mo=r&ad=...`, which carries no job key. The reader
looked for the key only in that href, found none, and then "read the posting" at
the tracking redirect, which is an interstitial with no posting on it.

Around that sat three more faults that each turned a recoverable gap into a
silent one: a failed re-read overwrote a description already captured, whole-page
text was accepted so sign-in walls could be stored as descriptions, and a sync
that read nothing reported a clean success.

The browser here is a local headless Chromium serving fixture HTML. Nothing in
this file touches an employer site or a saved session.
"""
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app
from app.models.job import Job
from app.services import job_description, source_sync
from app.sources import browser_boards

REAL_POSTING = (
    "About the role: we are hiring a machine learning engineer to build retrieval "
    "systems. Responsibilities include designing training pipelines, running "
    "offline evaluation, and deploying models to production services. "
    "Requirements: strong Python, experience with PyTorch, and a track record of "
    "measuring what you ship. Preferred qualifications: vector search, distributed "
    "data processing, and mentoring junior engineers. Benefits include health "
    "cover and a learning budget."
)

SIGN_IN_WALL = (
    "Sign in to continue. Please sign in to your account to view this job. "
    "Create an account to apply. Forgot your password? Sign in with Google."
)

EXPIRED = (
    "This job has expired. The posting you are looking for is no longer "
    "available. Browse similar jobs instead."
)


def results_page(cards: str) -> str:
    return f"<html><body><div id='results'>{cards}</div></body></html>"


def sponsored_card(jk: str, title: str, company: str) -> str:
    """A sponsored Indeed card: tracking href, job key only in the markup."""
    return (
        f"<div class='job_seen_beacon' data-jk='{jk}'>"
        f"<h2 class='jobTitle'><a class='jcs-JobTitle' id='job_{jk}' data-jk='{jk}' "
        f"href='/pagead/clk?mo=r&ad=-6NYlbfk{jk}'>{title}</a></h2>"
        f"<span data-testid='company-name'>{company}</span>"
        f"<div data-testid='text-location'>New York, NY</div></div>"
    )


def organic_card(jk: str, title: str, company: str) -> str:
    return (
        f"<div class='job_seen_beacon'>"
        f"<h2 class='jobTitle'><a class='jcs-JobTitle' href='/rc/clk?jk={jk}&bb=xyz'>{title}</a></h2>"
        f"<span data-testid='company-name'>{company}</span></div>"
    )


# --------------------------------------------------------------------------- #
# The job key, which is the root cause
# --------------------------------------------------------------------------- #

class JobKeyTests(unittest.TestCase):
    """Where the key is found. Only the href case ever worked."""

    class FakeLocator:
        def __init__(self, attrs=None, count=1):
            self._attrs = attrs or {}
            self._count = count

        def count(self):
            return self._count

        def get_attribute(self, name):
            return self._attrs.get(name)

        def locator(self, _selector):
            return self

        @property
        def first(self):
            return self

    def key(self, card_attrs=None, link_attrs=None, href="", nested=None):
        card = self.FakeLocator(card_attrs, count=1)
        if nested is not None:
            card.locator = lambda _s: self.FakeLocator(nested, count=1)
        link = self.FakeLocator(link_attrs, count=1)
        return browser_boards._indeed_job_key(card, link, href)

    def test_the_key_is_read_from_the_link_when_the_href_has_none(self):
        """The whole regression: a sponsored href carries no jk."""
        self.assertEqual(
            self.key(link_attrs={"data-jk": "88c9bfe92efa46de"},
                     href="/pagead/clk?mo=r&ad=-6NYlbfkN0AOZTkBmB"),
            "88c9bfe92efa46de",
        )

    def test_the_key_is_read_from_the_card_when_the_link_has_none(self):
        self.assertEqual(
            self.key(card_attrs={"data-jk": "03f72469f35eb4be"},
                     href="/pagead/clk?mo=r"),
            "03f72469f35eb4be",
        )

    def test_the_key_is_read_from_the_element_id(self):
        self.assertEqual(
            self.key(link_attrs={"id": "job_a1b2c3d4e5f60718"}, href="/pagead/clk"),
            "a1b2c3d4e5f60718",
        )

    def test_the_key_is_read_from_a_nested_element(self):
        self.assertEqual(
            self.key(nested={"data-jk": "beefcafe12345678"}, href="/pagead/clk"),
            "beefcafe12345678",
        )

    def test_the_href_still_works_for_organic_listings(self):
        self.assertEqual(
            self.key(href="/rc/clk?jk=deadbeef87654321&bb=x"), "deadbeef87654321"
        )

    def test_nothing_is_invented_when_there_is_no_key(self):
        self.assertEqual(self.key(href="/pagead/clk?mo=r&ad=xyz"), "")

    def test_text_that_is_not_a_key_is_refused(self):
        for bogus in ("not-a-key", "SHOUTING", "12", "zzzzzzzzzzzzzzzz"):
            with self.subTest(bogus=bogus):
                self.assertEqual(self.key(link_attrs={"data-jk": bogus}), "")


# --------------------------------------------------------------------------- #
# What counts as a posting
# --------------------------------------------------------------------------- #

class PostingCheckTests(unittest.TestCase):
    def test_a_real_posting_passes(self):
        self.assertEqual(
            job_description.check(REAL_POSTING, "Example Labs", "ML Engineer"),
            REAL_POSTING,
        )

    def test_a_sign_in_wall_is_not_a_description(self):
        with self.assertRaises(job_description.NotAPosting) as caught:
            job_description.check(SIGN_IN_WALL, "Example Labs", "Engineer")
        self.assertEqual(caught.exception.kind, "wall")

    def test_an_expired_posting_is_not_a_description(self):
        with self.assertRaises(job_description.NotAPosting):
            job_description.check(EXPIRED, "Example Labs", "Engineer")

    def test_nothing_captured_is_reported_as_missing_not_as_junk(self):
        """"Never captured" and "captured but wrong" need different fixes."""
        with self.assertRaises(job_description.NotAPosting) as caught:
            job_description.check("")
        self.assertEqual(caught.exception.kind, "missing")

    def test_the_old_invented_placeholder_is_recognised(self):
        with self.assertRaises(job_description.NotAPosting) as caught:
            job_description.check(
                "Senior Engineer at Capital One. See the original posting."
            )
        self.assertEqual(caught.exception.kind, "missing")

    def test_a_concise_genuine_posting_is_not_refused_for_being_short(self):
        concise = (
            "About the role: you will build retrieval models. Responsibilities: "
            "training pipelines and evaluation. Requirements: Python and PyTorch."
        )
        self.assertLess(job_description.words(concise), 60)
        self.assertEqual(job_description.check(concise, "Acme", "Engineer"), concise)

    def test_a_long_wall_is_still_refused_and_a_long_posting_is_not(self):
        self.assertFalse(job_description.usable(SIGN_IN_WALL * 3))
        self.assertTrue(job_description.usable(REAL_POSTING))

    def test_a_posting_for_a_different_job_is_caught_when_identity_matters(self):
        with self.assertRaises(job_description.NotAPosting) as caught:
            job_description.check(REAL_POSTING, "Northwind Trading", "Barista",
                                  require_identity=True)
        self.assertEqual(caught.exception.kind, "mismatch")

    def test_identity_is_not_demanded_of_text_the_user_pasted(self):
        self.assertTrue(job_description.usable(REAL_POSTING, "Northwind", "Barista"))

    def test_a_generic_company_word_does_not_count_as_identity(self):
        """"Group", "Inc" and the like appear in almost any posting."""
        self.assertFalse(
            job_description.identity_matches(REAL_POSTING, "Pension Group Inc", "")
        )


# --------------------------------------------------------------------------- #
# Storing: a failed read must never cost a description already held
# --------------------------------------------------------------------------- #

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

    def row(self, **over):
        base = {
            "external_id": "88c9bfe92efa46de", "source": "indeed",
            "company": "Example Labs", "title": "ML Engineer",
            "description": REAL_POSTING,
            "application_url": "https://www.indeed.com/viewjob?jk=88c9bfe92efa46de",
            "source_url": "https://www.indeed.com/pagead/clk?mo=r&ad=one",
        }
        base.update(over)
        return base

    def test_a_description_already_held_survives_an_empty_refresh(self):
        with self.sessions() as s:
            source_sync._store(s, [self.row()])
            source_sync._store(s, [self.row(description="")])
            job = s.query(Job).one()
            self.assertIn("retrieval systems", job.description)

    def test_a_refresh_that_does_find_the_posting_updates_it(self):
        with self.sessions() as s:
            source_sync._store(s, [self.row(description="")])
            source_sync._store(s, [self.row()])
            self.assertIn("retrieval systems", s.query(Job).one().description)

    def test_the_same_job_on_a_new_tracking_url_is_not_filed_twice(self):
        """Tracking URLs are regenerated each search - hence duplicate rows."""
        with self.sessions() as s:
            source_sync._store(s, [self.row()])
            source_sync._store(s, [self.row(
                source_url="https://www.indeed.com/pagead/clk?mo=r&ad=DIFFERENT"
            )])
            self.assertEqual(s.query(Job).count(), 1)

    def test_a_row_with_no_key_still_stores(self):
        with self.sessions() as s:
            source_sync._store(s, [self.row(
                external_id=None,
                application_url="https://example.test/job/1",
            )])
            self.assertEqual(s.query(Job).count(), 1)

    def test_reader_notes_do_not_reach_the_model(self):
        """`description_error` is a working note, not a column."""
        with self.sessions() as s:
            source_sync._store(s, [self.row(
                description="", description_error="wall"
            )])
            self.assertEqual(s.query(Job).count(), 1)

    def test_a_key_learned_later_is_filled_in_without_a_duplicate(self):
        with self.sessions() as s:
            source_sync._store(s, [self.row(external_id=None)])
            source_sync._store(s, [self.row()])
            self.assertEqual(s.query(Job).count(), 1)
            self.assertEqual(s.query(Job).one().external_id, "88c9bfe92efa46de")


# --------------------------------------------------------------------------- #
# Reading a fixture page in a real browser
# --------------------------------------------------------------------------- #

class DescribePagesTests(unittest.TestCase):
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
        self.page = self.context.new_page()

    def serve(self, bodies: dict):
        """Serve fixture HTML per URL. Unlisted URLs get an interstitial."""
        def handler(route):
            url = route.request.url
            for marker, body in bodies.items():
                if marker in url:
                    return route.fulfill(status=200, content_type="text/html", body=body)
            return route.fulfill(
                status=200, content_type="text/html",
                body="<html><body><p>Redirecting to the employer…</p></body></html>",
            )
        self.page.route("**/*", handler)

    def describe(self, jobs, bodies):
        self.serve(bodies)
        with mock.patch.object(browser_boards.browser_session, "human_pause",
                               lambda *a, **k: None):
            return browser_boards._describe_pages(self.page, jobs, "#jobDescriptionText")

    def job(self, **over):
        base = {"company": "Example Labs", "title": "ML Engineer",
                "description": "",
                "application_url": "https://www.indeed.com/pagead/clk?mo=r&ad=x",
                "read_url": "https://www.indeed.com/viewjob?jk=88c9bfe92efa46de"}
        base.update(over)
        return base

    def test_the_canonical_url_is_read_not_the_tracking_redirect(self):
        """Reproduces the regression: the tracking URL has no posting on it."""
        jobs = [self.job()]
        read = self.describe(jobs, {
            "viewjob": f"<div id='jobDescriptionText'>{REAL_POSTING}</div>",
        })
        self.assertEqual(read, 1)
        self.assertIn("retrieval systems", jobs[0]["description"])

    def test_a_tracking_redirect_with_no_posting_stores_nothing(self):
        jobs = [self.job(read_url="https://www.indeed.com/pagead/clk?mo=r&ad=x")]
        self.assertEqual(self.describe(jobs, {}), 0)
        self.assertEqual(jobs[0]["description"], "")
        self.assertTrue(jobs[0]["description_error"])

    def test_a_sign_in_wall_is_never_stored_as_a_description(self):
        jobs = [self.job()]
        self.assertEqual(self.describe(jobs, {
            "viewjob": f"<body><main>{SIGN_IN_WALL}</main></body>",
        }), 0)
        self.assertEqual(jobs[0]["description"], "")
        self.assertIn("wall", jobs[0]["description_error"])

    def test_an_expired_posting_is_never_stored_as_a_description(self):
        jobs = [self.job()]
        self.assertEqual(self.describe(jobs, {
            "viewjob": f"<body><main>{EXPIRED}</main></body>",
        }), 0)
        self.assertEqual(jobs[0]["description"], "")

    def test_a_posting_for_a_different_company_is_not_stored(self):
        jobs = [self.job(company="Northwind Trading", title="Barista")]
        self.assertEqual(self.describe(jobs, {
            "viewjob": f"<div id='jobDescriptionText'>{REAL_POSTING}</div>",
        }), 0)
        self.assertIn("different posting", jobs[0]["description_error"])

    def test_content_is_waited_for_rather_than_a_fixed_pause(self):
        """A slow page used to be captured as its own loading shell."""
        late = (
            "<body><div id='app'>Loading…</div><script>"
            "setTimeout(function(){var d=document.createElement('div');"
            "d.id='jobDescriptionText';d.textContent=" + repr(REAL_POSTING).replace("'", '"') +
            ";document.body.appendChild(d);}, 1200);</script></body>"
        )
        jobs = [self.job()]
        self.assertEqual(self.describe(jobs, {"viewjob": late}), 1)
        self.assertIn("retrieval systems", jobs[0]["description"])

    def test_one_unreadable_job_does_not_stop_the_others(self):
        good, bad = self.job(), self.job(
            read_url="https://www.indeed.com/pagead/clk?mo=r&ad=nothing"
        )
        read = self.describe([bad, good], {
            "viewjob": f"<div id='jobDescriptionText'>{REAL_POSTING}</div>",
        })
        self.assertEqual(read, 1)
        self.assertIn("retrieval systems", good["description"])


# --------------------------------------------------------------------------- #
# Recovery through the API, on the same record
# --------------------------------------------------------------------------- #

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

        def dependency():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = dependency
        self.addCleanup(app.dependency_overrides.clear)
        self.addCleanup(self.engine.dispose)
        self.client = TestClient(app)

        with self.sessions() as session:
            session.add(Job(
                id="job-1", company="Example Labs", title="ML Engineer",
                source="indeed", external_id="88c9bfe92efa46de", description="",
                application_url="https://www.indeed.com/viewjob?jk=88c9bfe92efa46de",
            ))
            session.commit()

    def test_recovery_updates_the_same_record_and_keeps_its_id(self):
        response = self.client.post("/jobs/job-1/description",
                                    json={"description": REAL_POSTING})
        self.assertEqual(response.status_code, 200, response.text)
        job = self.client.get("/jobs/job-1").json()
        self.assertEqual(job["id"], "job-1")
        self.assertIn("retrieval systems", job["description"])

    def test_the_saved_description_comes_back_in_the_response(self):
        """So the UI can show it without a reload or an app restart."""
        body = self.client.post("/jobs/job-1/description",
                                json={"description": REAL_POSTING}).json()
        self.assertIn("retrieval systems", body["description"])

    def test_a_wall_pasted_by_hand_is_refused(self):
        response = self.client.post("/jobs/job-1/description",
                                    json={"description": SIGN_IN_WALL})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/jobs/job-1").json()["description"], "")

    def test_the_list_survives_a_mixture_of_captured_and_missing(self):
        with self.sessions() as session:
            session.add(Job(
                id="job-2", company="Acme", title="Engineer", source="linkedin",
                description=REAL_POSTING,
                application_url="https://example.test/2",
            ))
            session.commit()
        listed = self.client.get("/jobs")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

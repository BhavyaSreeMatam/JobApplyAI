"""Date repair regressions. Synthetic pages and an isolated SQLite database.

These tests never open a real employer site, read a saved profile or call AI.
"""
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app
from app.models.job import Job
from app.services import date_refresh, source_sync
from app.sources import boards, browser_boards, single_job
from app.sources.posting_dates import extract_card_date, extract_page_date, parse_posted

NOW = datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc)
JOB = {"id": "fixture-id", "title": "Software Engineer", "company": "Example Labs",
       "application_url": "https://www.indeed.com/viewjob?jk=0123456789abcdef", "source": "indeed"}
DATE = "2026-09-10T08:00:00Z"
DESCRIPTION = (
    "Example Labs is hiring a Software Engineer. About the role: you will build "
    "reliable services and data pipelines. Responsibilities include design, "
    "testing, code review, and deployment. Requirements: Python, SQL, Linux, "
    "and experience with distributed systems. Benefits include health insurance "
    "and a learning budget. "
) * 2


def structured(**changes):
    return {"@type": "JobPosting", "title": JOB["title"], "hiringOrganization": {"name": JOB["company"]},
            "url": JOB["application_url"], "datePosted": DATE, **changes}


def html_page(node=None, extra=""):
    metadata = f'<script type="application/ld+json">{json.dumps(node)}</script>' if node is not None else ""
    return f'<html><head>{metadata}</head><body><main><h1>Software Engineer</h1>{extra}<div id="jobDescriptionText">{DESCRIPTION}</div></main></body></html>'


class ParserTests(unittest.TestCase):
    def test_minutes_hours_and_midnight(self):
        for text, seconds in (("2 hours ago", 7200), ("90 minutes ago", 5400), ("2h ago", 7200), ("3 hr ago", 10800), ("5d ago", 432000)):
            with self.subTest(text=text):
                self.assertEqual(parse_posted(text, now=NOW), NOW - timedelta(seconds=seconds))

    def test_posted_prefix_abbreviations_and_lower_bound(self):
        for text, days in (("Posted 30+ Days Ago", 30), ("Posted: 2 weeks ago", 14), ("3mo ago", 90), ("Yesterday", 1), ("Posted Today", 0)):
            self.assertEqual(parse_posted(text, now=NOW), NOW - timedelta(days=days))

    def test_absolute_epochs_timezone_and_named_month(self):
        expected = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
        for value in (DATE, "2026-09-10T04:00:00-04:00", expected.timestamp(), expected.timestamp() * 1000, str(int(expected.timestamp() * 1000))):
            self.assertEqual(parse_posted(value, now=NOW), expected)
        self.assertEqual(parse_posted("September 10, 2026", now=NOW).date(), expected.date())
        self.assertEqual(parse_posted("2026-09-10", now=NOW).tzinfo, timezone.utc)

    def test_unknown_ambiguous_unrelated_and_future_dates_stay_unknown(self):
        for text in (None, "", "Easy Apply", "1m ago", "09/10/2026", "Expires in 2 days", "Updated 2 days ago", "Reposted 3 days ago", "work 5 days weekly", True, float("nan"), "2099-01-01"):
            self.assertIsNone(parse_posted(text, now=NOW), msg=repr(text))


class ExtractionTests(unittest.TestCase):
    def extract(self, html, job=JOB, final=None):
        return extract_page_date(html, job, final or job["application_url"], now=NOW)

    def test_all_card_candidates_are_checked_after_easy_apply(self):
        html = '<span class="job-card-container__footer-item">Easy Apply</span><span class="job-card-container__footer-item">3 days ago</span>'
        evidence = extract_card_date(html, "linkedin", now=NOW)
        self.assertEqual(evidence.value, NOW - timedelta(days=3))

    def test_machine_time_beats_rounded_text(self):
        html = f'<time datetime="{DATE}">3 days ago</time>'
        evidence = extract_card_date(html, "linkedin", now=NOW)
        self.assertEqual(evidence.value, parse_posted(DATE, now=NOW))
        self.assertFalse(evidence.estimated)

    def test_indeed_skips_unrelated_date_class(self):
        html = '<span class="candidate-updates">Updated 1 day ago</span><span data-testid="myJobsStateDate">Posted 5 days ago</span>'
        self.assertEqual(extract_card_date(html, "indeed", now=NOW).value, NOW - timedelta(days=5))

    def test_reposting_is_not_original_publication(self):
        html = '<div>Reposted <time datetime="2026-09-13">1 day ago</time></div>'
        self.assertIsNone(extract_card_date(html, "linkedin", now=NOW))
        html = '<span>Reposted</span><span class="job-card-container__footer-item">1 day ago</span>'
        self.assertIsNone(extract_card_date(html, "linkedin", now=NOW))

    def test_jsonld_graph_selects_this_job_not_first_recommendation(self):
        other = structured(url="https://www.indeed.com/viewjob?jk=abcdefabcdefabcd", datePosted="2026-09-13")
        evidence = self.extract(html_page({"@graph": [other, structured()]}))
        self.assertEqual(evidence.value, parse_posted(DATE, now=NOW))

    def test_jsonld_without_url_requires_both_title_and_company(self):
        node = structured(); node.pop("url")
        self.assertIsNotNone(self.extract(html_page(node)))
        node["hiringOrganization"] = {"name": "Another company"}
        self.assertIsNone(self.extract(html_page(node)))

    def test_jsonld_does_not_substitute_modified_or_expiry(self):
        node = structured(dateModified=DATE, validThrough=DATE); node.pop("datePosted")
        self.assertIsNone(self.extract(html_page(node)))

    def test_matching_microdata_and_explicit_handshake_label(self):
        for extra in (f'<time itemprop="datePosted" datetime="{DATE}"></time>', '<div><span>Posted</span><span>2 days ago</span></div>'):
            self.assertIsNotNone(self.extract(html_page(extra=extra)))

    def test_header_can_contain_location_and_applicant_count(self):
        extra = '<div class="job-details-jobs-unified-top-card__primary-description-container">NY · 2 days ago · 100 applicants</div>'
        self.assertEqual(self.extract(html_page(extra=extra)).value, NOW - timedelta(days=2))

    def test_body_dates_deadlines_and_sidebar_do_not_count(self):
        for extra in ('<aside><time itemprop="datePosted" datetime="2026-09-13"></time></aside>', '<p>Apply by September 12, 2026</p>', '<h2>Job description</h2><p>Posted 3 days ago</p>', '<h2>Similar jobs</h2><p>Posted 3 days ago</p>'):
            self.assertIsNone(self.extract(html_page(extra=extra)), msg=extra)

    def test_foreign_microdata_is_ignored(self):
        extra = '<section itemscope itemtype="https://schema.org/JobPosting"><span itemprop="title">Accountant</span><time itemprop="datePosted" datetime="2026-09-13"></time></section>'
        self.assertIsNone(self.extract(html_page(extra=extra)))

    def test_other_job_redirect_cannot_supply_date(self):
        final = "https://www.indeed.com/viewjob?jk=abcdefabcdefabcd"
        self.assertIsNone(self.extract(html_page(structured(url=final)), final=final))
        self.assertIsNone(self.extract(html_page(extra="<p>Posted 2 days ago</p>"), final=final))

    def test_old_sponsored_redirect_needs_final_url_title_and_company(self):
        job = {**JOB, "application_url": "https://www.indeed.com/pagead/clk?ad=opaque"}
        self.assertIsNotNone(self.extract(html_page(structured()), job, JOB["application_url"]))
        wrong = structured(hiringOrganization={"name": "Other Company"})
        self.assertIsNone(self.extract(html_page(wrong), job, JOB["application_url"]))

    def test_malformed_json_falls_back_to_labelled_date(self):
        html = html_page(extra='<script type="application/ld+json">broken</script><p>Posted: September 10, 2026</p>')
        self.assertEqual(self.extract(html).value.date(), datetime(2026, 9, 10).date())

    def test_no_evidence_is_unknown(self):
        self.assertIsNone(self.extract(html_page()))


class FakeLocator:
    def __init__(self, nodes): self.nodes = nodes
    @property
    def first(self): return FakeLocator(self.nodes[:1])
    def count(self): return len(self.nodes)
    def inner_text(self, **kwargs): return self.nodes[0].get_text(" ", strip=True)
    def inner_html(self): return self.nodes[0].decode_contents()


class FakePage:
    """Only browser navigation is stubbed; date parsing uses fixture HTML."""
    def __init__(self, pages): self.pages, self.visited, self.closed = pages, [], False
    def goto(self, url, **kwargs):
        self.url = url; self.visited.append(url)
        self.html = self.pages[url]
    def content(self): return self.html
    def locator(self, selector): return FakeLocator(BeautifulSoup(self.html, "html.parser").select(selector))
    def wait_for_selector(self, *args, **kwargs): pass
    def close(self): self.closed = True


class ReaderIntegrationTests(unittest.TestCase):
    def test_description_visit_recovers_missing_date(self):
        page = FakePage({JOB["application_url"]: html_page(structured())})
        jobs = [{**JOB, "posted_at": None}]
        self.assertEqual(browser_boards._describe_pages(page, jobs, "#jobDescriptionText"), 1)
        self.assertEqual(jobs[0]["posted_at"], parse_posted(DATE))
        self.assertIn("Responsibilities", jobs[0]["description"])

    def test_login_stops_recovery_without_inventing_dates(self):
        page = FakePage({JOB["application_url"]: "<body>Please verify you are human</body>"})
        worker = SimpleNamespace(context=lambda: SimpleNamespace(new_page=lambda: page))
        result = date_refresh._browser_read(worker, [JOB, {**JOB, "id": "next"}])
        self.assertEqual([r["status"] for r in result], ["blocked"])
        self.assertEqual(len(page.visited), 1)
        self.assertTrue(page.closed)

    def test_indeed_tracking_link_uses_verified_key(self):
        page = FakePage({JOB["application_url"]: html_page(structured())})
        worker = SimpleNamespace(context=lambda: SimpleNamespace(new_page=lambda: page))
        row = {**JOB, "application_url": "https://www.indeed.com/pagead/clk?ad=x", "external_id": "0123456789abcdef"}
        result = date_refresh._browser_read(worker, [row])
        self.assertEqual(result[0]["status"], "found")
        self.assertEqual(page.visited, [JOB["application_url"]])

    def test_pasted_link_extracts_date_without_asking_model_for_it(self):
        posting = SimpleNamespace(is_job_posting=True, title=JOB["title"], company=JOB["company"], location="NY", employment_type="Full-time", description=DESCRIPTION)
        with patch.object(single_job, "_from_board", AsyncMock(return_value=None)), patch.object(single_job, "_page_text", AsyncMock(return_value=(DESCRIPTION, JOB["application_url"], html_page(structured())))), patch.object(single_job.claude_client, "parse", return_value=posting):
            result = asyncio.run(single_job.import_job(JOB["application_url"]))
        self.assertEqual(result["posted_at"], parse_posted(DATE))

    def test_greenhouse_modified_date_is_not_publication(self):
        payload = {"jobs": [{"id": 123, "absolute_url": "https://boards.greenhouse.io/example/jobs/123", "title": JOB["title"], "updated_at": DATE}]}
        with patch.object(boards, "_get_json", AsyncMock(return_value=payload)):
            result = asyncio.run(boards.greenhouse("example", JOB["company"]))
        self.assertIsNone(result[0]["posted_at"])

    def test_greenhouse_recovery_uses_first_published(self):
        job = {**JOB, "application_url": "https://boards.greenhouse.io/example/jobs/123456", "external_id": "123456"}
        with patch.object(boards, "_get_json", AsyncMock(return_value={"id": 123456, "first_published": DATE})), patch.object(date_refresh.browser_session, "run", AsyncMock(side_effect=AssertionError("No browser needed"))):
            result = asyncio.run(date_refresh.read_dates([job]))
        self.assertEqual(result[0]["date"], parse_posted(DATE))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        def dependency():
            with self.sessions() as session: yield session
        app.dependency_overrides[get_db] = dependency
        self.addCleanup(app.dependency_overrides.clear)
        self.addCleanup(self.engine.dispose)
        self.client = TestClient(app)
        with self.sessions() as s:
            s.add(Job(**JOB, description=DESCRIPTION))
            s.commit()

    def test_existing_row_gains_date_without_duplicate(self):
        with self.sessions() as s:
            source_sync._store(s, [{**JOB, "description": DESCRIPTION, "posted_at": parse_posted(DATE)}])
            rows = s.scalars(select(Job)).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].id, JOB["id"])
            self.assertIsNotNone(rows[0].posted_at)

    def test_relative_resync_cannot_shift_stored_date_or_erase_it(self):
        with self.sessions() as s:
            for value in (parse_posted(DATE), None, NOW):
                source_sync._store(s, [{**JOB, "description": "", "posted_at": value}])
            job = s.get(Job, JOB["id"])
            self.assertEqual(job.posted_at.replace(tzinfo=timezone.utc), parse_posted(DATE))
            self.assertEqual(job.description, DESCRIPTION)

    def test_endpoint_persists_date_and_get_jobs_returns_it(self):
        fake = {"id": JOB["id"], "title": JOB["title"], "status": "found", "date": parse_posted(DATE)}
        with patch.object(date_refresh, "read_dates", AsyncMock(return_value=[fake])):
            result = self.client.post("/jobs/dates/refresh", json={"job_ids": [JOB["id"]]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["recovered"], 1)
        self.assertEqual(result.json()["jobs_with_date"], 1)
        saved = self.client.get("/jobs").json()[0]
        self.assertEqual(saved["id"], JOB["id"])
        self.assertTrue(saved["posted_at"].startswith("2026-09-10T08:00:00"))
        self.assertEqual(saved["description"], DESCRIPTION)

    def test_unknown_dates_remain_null(self):
        with patch.object(date_refresh, "read_dates", AsyncMock(return_value=[{"id": JOB["id"], "status": "not_found"}])):
            result = self.client.post("/jobs/dates/refresh", json={"job_ids": [JOB["id"]]}).json()
        self.assertEqual(result["recovered"], 0)
        self.assertIsNone(self.client.get("/jobs").json()[0]["posted_at"])

    def test_date_saved_during_browser_read_is_preserved(self):
        async def concurrent(_jobs):
            with self.sessions() as s:
                s.get(Job, JOB["id"]).posted_at = parse_posted(DATE)
                s.commit()
            return [{"id": JOB["id"], "status": "found", "date": NOW}]
        with patch.object(date_refresh, "read_dates", concurrent):
            result = self.client.post("/jobs/dates/refresh", json={"job_ids": [JOB["id"]]}).json()
        self.assertEqual(result["recovered"], 0)
        self.assertTrue(self.client.get("/jobs").json()[0]["posted_at"].startswith("2026-09-10"))

    def test_overlarge_batch_rejected_before_browser_work(self):
        with patch.object(date_refresh, "read_dates", AsyncMock()) as reader:
            response = self.client.post("/jobs/dates/refresh", json={"job_ids": [str(i) for i in range(11)]})
        self.assertEqual(response.status_code, 422)
        reader.assert_not_called()

    def test_pasted_existing_link_can_gain_date_without_duplicate(self):
        row = {**JOB, "description": DESCRIPTION, "posted_at": parse_posted(DATE), "import_method": "page-extract"}
        with patch.object(single_job, "import_job", AsyncMock(return_value=row)):
            response = self.client.post("/sources/single-job", json={"url": JOB["application_url"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["already_known"])
        rows = self.client.get("/jobs").json()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["posted_at"].startswith("2026-09-10"))


if __name__ == "__main__":
    unittest.main()

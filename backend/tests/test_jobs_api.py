"""The jobs list, including jobs whose description was never captured.

A job with no description is a normal state: some sites link through a redirect
that carries no posting text, and storing the description as empty is how "we
never captured it" is told apart from "here is the posting".

The read schema inherited a `min_length=1` from the create schema, so those rows
failed response validation - and because the list is serialised as one model, a
single empty string returned 500 for the entire dashboard. The app looked like
the backend was down. These make that a test failure instead.
"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app
from app.models.job import Job


class JobListTests(unittest.TestCase):
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
            session.add_all([
                Job(company="Example Labs", title="With a description",
                    source="linkedin", external_id="jobs-1",
                    description="A real posting. " * 40,
                    application_url="https://example.test/1"),
                Job(company="Capital One", title="Without a description",
                    source="indeed", external_id="jobs-2", description="",
                    application_url="https://www.indeed.com/viewjob?jk=abc"),
            ])
            session.commit()

    def test_the_list_survives_a_job_with_no_description(self):
        response = self.client.get("/jobs")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 2)

    def test_the_empty_description_comes_back_as_empty(self):
        by_title = {job["title"]: job for job in self.client.get("/jobs").json()}
        self.assertEqual(by_title["Without a description"]["description"], "")
        self.assertIn("A real posting", by_title["With a description"]["description"])

    def test_one_job_can_be_read_on_its_own(self):
        listed = self.client.get("/jobs").json()
        for job in listed:
            with self.subTest(title=job["title"]):
                self.assertEqual(self.client.get(f"/jobs/{job['id']}").status_code, 200)

    def test_a_description_can_be_pasted_in(self):
        job = next(j for j in self.client.get("/jobs").json()
                   if not j["description"])
        posting = (
            "We are hiring an engineer to build retrieval systems. You will design "
            "pipelines, run evaluation, and ship services. Requirements: Python, "
            "PyTorch, and experience building and measuring machine learning systems "
            "end to end. Preferred: vector search and distributed data processing. "
        ) * 2
        response = self.client.post(
            f"/jobs/{job['id']}/description", json={"description": posting}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(response.json()["words"], 60)
        self.assertIn("retrieval systems",
                      self.client.get(f"/jobs/{job['id']}").json()["description"])

    def test_a_paste_that_is_not_a_posting_is_refused(self):
        job = next(j for j in self.client.get("/jobs").json() if not j["description"])
        response = self.client.post(
            f"/jobs/{job['id']}/description", json={"description": "Engineer wanted."}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("does not read like a job posting", response.json()["detail"])

    def test_a_concise_but_genuine_posting_is_accepted(self):
        """The old sixty-word floor refused real postings for being short."""
        job = next(j for j in self.client.get("/jobs").json() if not j["description"])
        posting = (
            "About the role: you will build and evaluate retrieval models for our "
            "search team. Responsibilities include designing training pipelines, "
            "running offline evaluation, and shipping models to production. "
            "Requirements: strong Python, experience with PyTorch, and a track "
            "record of measuring what you ship. Preferred: vector search."
        )
        # Comfortably a real posting, and still under the sixty-word floor that
        # used to refuse it outright.
        self.assertLess(len(posting.split()), 60)
        self.assertGreater(len(posting.split()), 40)
        response = self.client.post(
            f"/jobs/{job['id']}/description", json={"description": posting}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("retrieval models",
                      self.client.get(f"/jobs/{job['id']}").json()["description"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

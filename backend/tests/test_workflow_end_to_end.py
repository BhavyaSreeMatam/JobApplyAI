"""The whole broken workflow, start to finish, through the real HTTP API.

load a job -> see it has no description -> recover the description ->
see it displayed -> tailor against it -> download the resume and cover letter.

Each of those steps had its own fault, and each was individually invisible: the
capture failed silently, the empty description then took down the whole jobs
list, the recovered text was saved but never shown because the UI held the job it
opened with, and tailoring was handed that same stale object. Testing them
separately is how a workflow stays broken while every part of it passes.

The model is mocked throughout - no paid call is made, and one of the assertions
below is precisely that no paid call is made when the description is missing.
Nothing here touches an employer site.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pdfplumber
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app
from app.models.candidate import CandidateProfile
from app.models.job import Job
from app.models.package import ApplicationPackage  # noqa: F401 - registers the table
from app.models.resume import ResumeDocument  # noqa: F401 - registers the table
from app.schemas.tailoring import CoverLetter
from tests.test_generation_entrypoints import ANALYSIS, DESCRIPTION, PROFILE, fake_plan


class WorkflowTests(unittest.TestCase):
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
            session.add(CandidateProfile(id="local", data=dict(PROFILE), revision=1))
            session.add(Job(
                id="job-empty", company="Example Labs",
                title="Machine Learning Engineer", source="indeed",
                external_id="88c9bfe92efa46de", description="",
                application_url="https://www.indeed.com/viewjob?jk=88c9bfe92efa46de",
                source_url="https://www.indeed.com/pagead/clk?mo=r&ad=x",
                location="New York, NY",
            ))
            session.commit()

        self.calls = []
        for target, replacement in (
            ("app.services.tailoring.analyze_job",
             lambda job: (self.calls.append("analyze"), dict(ANALYSIS))[1]),
            ("app.services.tailoring.select_for_job",
             lambda *a, **k: (self.calls.append("select"), fake_plan(*a, **k))[1]),
            ("app.services.tailoring.write_cover_letter",
             lambda *a, **k: (self.calls.append("cover"), CoverLetter(
                 body=(
                     "I am applying for the Machine Learning Engineer role at Example "
                     "Labs. I built retrieval pipelines in Python and PyTorch, and "
                     "shipped FastAPI endpoints for model inference. I also built a "
                     "retrieval system with FAISS and evaluated it with RAGAS, which "
                     # 300 words: the letter must land in 250-350 or it fails
                     # validation and is deleted rather than delivered.
                     "is the kind of measurement this posting asks for. " * 6
                 ),
                 unsupported_claims_avoided=[],
             ))[1]),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ steps
    def test_the_job_lists_and_opens_while_its_description_is_missing(self):
        listed = self.client.get("/jobs")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()), 1)
        one = self.client.get("/jobs/job-empty")
        self.assertEqual(one.status_code, 200)
        self.assertEqual(one.json()["description"], "")

    def test_tailoring_without_a_description_costs_no_model_call(self):
        response = self.client.post("/apply/prepare", json={"job_id": "job-empty"})
        # Deliberately not `>= 400`: a 404 from a wrong path would satisfy that
        # while proving nothing, which is exactly what it did at first.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("description", response.text.casefold())
        self.assertEqual(self.calls, [],
                         "a missing description must be caught before any paid call")

    def test_the_whole_workflow_from_recovery_to_two_verified_pdfs(self):
        # 1. recover the description
        saved = self.client.post("/jobs/job-empty/description",
                                 json={"description": DESCRIPTION})
        self.assertEqual(saved.status_code, 200, saved.text)

        # 2. it is readable immediately, with no restart - this is what the UI
        #    re-reads after a recovery instead of trusting the object it holds
        shown = self.client.get("/jobs/job-empty").json()["description"]
        self.assertIn("retrieval systems", shown)
        self.assertEqual(shown, saved.json()["description"])

        # 3. tailor against it
        prepared = self.client.post("/apply/prepare", json={"job_id": "job-empty"})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        package = prepared.json()
        self.assertIn("analyze", self.calls)
        self.assertTrue(package["cover_letter_path"],
                        "a letter that passed validation must be delivered")

        # 4. the documents download and are real PDFs with real text
        application_id = package["application_id"]
        for kind, needle in (("resume", "Test Person"), ("cover-letter", "Example Labs")):
            with self.subTest(document=kind):
                got = self.client.get(f"/apply/{application_id}/{kind}")
                self.assertEqual(got.status_code, 200, got.text)
                self.assertEqual(got.headers["content-type"], "application/pdf")
                self.assertTrue(got.content.startswith(b"%PDF"))
                text = self._text_of(got.content)
                self.assertIn(needle, text)

        # 5. and the description that was tailored against is the one displayed
        self.assertIn("retrieval systems",
                      self.client.get("/jobs/job-empty").json()["description"])

    def test_the_tailored_resume_is_not_the_whole_history(self):
        """The shorter tailored document stays separate from the full record."""
        self.client.post("/jobs/job-empty/description", json={"description": DESCRIPTION})
        package = self.client.post("/apply/prepare", json={"job_id": "job-empty"}).json()
        resume = self._text_of(
            self.client.get(f"/apply/{package['application_id']}/resume").content
        )
        profile = self.client.get("/agent/profile").json()
        self.assertTrue(profile["data"]["experience"],
                        "the full history must still be on the profile")
        # The tailored document is a selection, not the record: the profile keeps
        # both roles while the one-page resume need not carry everything.
        self.assertEqual(len(profile["data"]["experience"]), 2)
        self.assertLess(len(resume), 20000)

    def test_a_wall_cannot_be_saved_as_the_description_and_tailored_against(self):
        wall = ("Sign in to continue. Please sign in to your account to view this "
                "job. Create an account to apply. Forgot your password?")
        refused = self.client.post("/jobs/job-empty/description",
                                   json={"description": wall})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(self.client.get("/jobs/job-empty").json()["description"], "")
        self.assertEqual(self.calls, [])

    # ------------------------------------------------------------------ util
    @staticmethod
    def _text_of(data: bytes) -> str:
        import io

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

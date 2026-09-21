"""Importing a resume, through the endpoint the browser actually calls.

The crash this guards against needed no unusual input: any resume containing a
single job, degree, project or publication ended the request with
`AttributeError: 'dict' object has no attribute 'revision'`. The loop that fanned
parsed entries into sections named its variable `row`, which was already the name
of the profile record fetched above it, and the response read `row.revision`.

Unit tests on the parser could not see it, because the bug was in the endpoint.
So these drive the endpoint, with the model mocked - no paid calls, no network.
"""
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app
from app.models.candidate import CandidateProfile
from app.schemas.tailoring import ParsedProfile, ResumeEntry

RESUME_TEXT = (
    "Test Person\ntest@example.test | +1 5550001111\n"
    "EXPERIENCE\nML Engineer, Example Labs, 2023 - Present\n"
    "- Built retrieval pipelines in Python.\n"
    "EDUCATION\nM.S. Computer Science, Example University, 2021\n"
    "PROJECTS\nSourceLens\n- A retrieval system.\n"
    "PUBLICATIONS\nA paper at a conference. First author.\n"
) * 3


def parsed_profile():
    """What the parser returns for that document - every section populated."""
    return ParsedProfile(
        first_name="Test", last_name="Person", email="test@example.test",
        phone="+1 5550001111", location="New York, NY", website="", linkedin="",
        publications_url="", summary="An engineer.",
        skills=["Python", "PyTorch"],
        entries=[
            ResumeEntry(section="experience", heading="ML Engineer",
                        organization="Example Labs", dates="2023 - Present",
                        location="New York, NY",
                        bullets=["Built retrieval pipelines in Python."]),
            ResumeEntry(section="education", heading="M.S. Computer Science",
                        organization="Example University", dates="2021",
                        location="", bullets=[]),
            ResumeEntry(section="projects", heading="SourceLens", organization="",
                        dates="2024", location="", bullets=["A retrieval system."]),
            ResumeEntry(section="publications", heading="A paper at a conference",
                        organization="A Conference", dates="2025", location="",
                        bullets=["First author."]),
        ],
    )


class ImportEndpointTests(unittest.TestCase):
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

        self.parse = patch("app.api.config.tailoring.parse_resume_text",
                           return_value=parsed_profile())
        self.parse.start()
        self.addCleanup(self.parse.stop)

    def upload(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            handle.write(RESUME_TEXT.encode("utf-8"))
            path = handle.name
        with open(path, "rb") as handle:
            return self.client.post(
                "/config/import-resume",
                files={"resume": ("resume.txt", handle, "text/plain")},
            )

    def seed(self, data):
        with self.sessions() as session:
            session.add(CandidateProfile(id="local", data=data, revision=7))
            session.commit()

    def test_a_resume_with_every_section_imports_without_crashing(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["counts"],
                         {"experience": 1, "education": 1, "projects": 1, "publications": 1})

    def test_the_profile_revision_is_returned_not_an_entry_dict(self):
        """The crash was `row.revision` where `row` had become a parsed entry."""
        self.seed({"first_name": "Test"})
        body = self.upload().json()
        self.assertEqual(body["current_revision"], 7)

    def test_education_keeps_degree_and_major_apart(self):
        education = self.upload().json()["parsed"]["education"][0]
        self.assertEqual(education["degree"], "M.S.")
        self.assertEqual(education["major"], "Computer Science")

    def test_an_import_does_not_replace_what_the_candidate_typed(self):
        """Whole-section replacement used to delete every manual correction."""
        self.seed({
            "experience": [
                {"heading": "ML Engineer", "organization": "Example Labs",
                 "location": "Remote", "start_year": "2023", "current": True,
                 "bullets": ["A bullet I wrote myself."]},
                {"heading": "Intern", "organization": "Somewhere Else",
                 "bullets": ["A job this resume never mentions."]},
            ],
        })
        merged = self.upload().json()["merged_preview"]
        headings = [entry["heading"] for entry in merged["experience"]]
        self.assertEqual(headings, ["ML Engineer", "Intern"])

        matched = merged["experience"][0]
        self.assertEqual(matched["location"], "Remote", "a typed value must win")
        self.assertTrue(matched["current"], "manually set flags must survive")
        self.assertIn("A bullet I wrote myself.", matched["bullets"])
        self.assertIn("Built retrieval pipelines in Python.", matched["bullets"])

    def test_a_disagreement_is_reported_rather_than_applied(self):
        self.seed({"experience": [
            {"heading": "ML Engineer", "organization": "Example Labs",
             "location": "Remote", "start_year": "2022"},
        ]})
        body = self.upload().json()
        fields = {(c["field"], c["yours"], c["from_document"]) for c in body["conflicts"]}
        self.assertIn(("location", "Remote", "New York, NY"), fields)
        self.assertEqual(body["merged_preview"]["experience"][0]["location"], "Remote")

    def test_missing_sections_are_reported_rather_than_passing_quietly(self):
        thin = parsed_profile()
        thin.entries = [entry for entry in thin.entries if entry.section == "experience"]
        with patch("app.api.config.tailoring.parse_resume_text", return_value=thin):
            body = self.upload().json()
        self.assertIn("education", body["coverage_warning"])
        self.assertEqual(body["counts"]["publications"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

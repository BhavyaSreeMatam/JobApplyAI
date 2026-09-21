"""Every route into document generation, exercised end to end with the model mocked.

These exist because 261 passing tests did not notice that `build_package` had two
definitions. The second one shadowed the first, so the legacy pipeline - the one
with no selection, no grounding checks and blind keyword adoption - was what ran
every time Apply was pressed, and the only symptom was an AttributeError on the
first real use.

Nothing here calls a paid model or touches a network: the analysis, selection and
cover-letter calls are patched. What is real is the orchestration, the file
writing and the PDF, which is where that defect lived.
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.application import Application
from app.models.candidate import CandidateProfile
from app.models.job import Job
from app.models.package import ApplicationPackage  # noqa: F401 - registers the table
from app.models.resume import ResumeDocument  # noqa: F401 - registers the table
from app.schemas.tailoring import CoverLetter
from app.services import apply_pipeline

PROFILE = {
    "first_name": "Test", "last_name": "Person", "email": "test@example.test",
    "phone": "+1 5550001111", "location": "New York, NY",
    "skills": ["Python", "PyTorch", "FastAPI"],
    "adopted_skills": [],
    "experience": [
        {"heading": "ML Engineer", "organization": "Example Labs",
         "location": "New York, NY", "start_month": "01", "start_year": "2023",
         "current": True,
         "bullets": ["Built retrieval pipelines in Python and PyTorch.",
                     "Shipped FastAPI endpoints for model inference."]},
        {"heading": "Data Intern", "organization": "Other Co",
         "start_month": "06", "start_year": "2021", "end_month": "08",
         "end_year": "2021", "bullets": ["Cleaned survey data in Python."]},
    ],
    "education": [
        {"degree": "M.S.", "major": "Computer Science",
         "organization": "Example University", "start_year": "2021", "end_year": "2023"},
    ],
    "projects": [
        {"heading": "SourceLens", "start_year": "2024",
         "bullets": ["Built a retrieval system with FAISS and RAGAS evaluation."]},
    ],
    "publications": [
        {"heading": "A paper at a conference", "organization": "A Conference",
         "start_year": "2025", "bullets": ["First author."]},
    ],
}

DESCRIPTION = (
    "We are hiring a Machine Learning Engineer to build retrieval systems over "
    "internal documents. You will design and operate RAG pipelines, ship FastAPI "
    "services, run offline evaluation, and improve retrieval quality at scale. "
    "Requirements: strong Python, experience with PyTorch, familiarity with "
    "information retrieval and embeddings, and a track record of building and "
    "evaluating machine learning systems end to end. Preferred: vector search, "
    "distributed data processing, and publications at a peer-reviewed venue."
)

ANALYSIS = {
    "normalized_title": "Machine Learning Engineer",
    "seniority": "Mid-level",
    "keywords": [
        {"term": "Python", "importance": "required"},
        {"term": "PyTorch", "importance": "required"},
        {"term": "FastAPI", "importance": "preferred"},
        {"term": "Kubernetes", "importance": "required"},
    ],
    "responsibilities": ["Build retrieval pipelines over internal documents"],
    "eligibility": [],
    "hard_requirements": ["Strong Python"],
    "outcomes": ["Higher retrieval quality"],
    "company_context": "A team building retrieval systems.",
    "ats_notes": "",
}


def fake_plan(resume, job, analysis, page_target, extra_skills=None, shortfall=""):
    """A plan that keeps everything, so the deterministic machinery does the work."""
    return {
        "decisions": [
            {"index": item["index"], "action": "keep", "text": "", "priority": 2,
             "supports": "relevant"}
            for item in resume.evidence()
        ],
        "skills_added": [],
        "unsupported_requirements": ["Kubernetes in production"],
        "notes": "Kept the retrieval and evaluation evidence.",
    }


class GenerationEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

        self.session = self.sessions()
        self.addCleanup(self.session.close)
        self.session.add(CandidateProfile(id="local", data=dict(PROFILE), revision=1))
        self.job = Job(
            company="Example Labs", title="Machine Learning Engineer",
            source="test", external_id="entrypoint-1", description=DESCRIPTION,
            application_url="https://example.test/apply", location="New York, NY",
        )
        self.session.add(self.job)
        self.session.flush()
        self.application = Application(job_id=self.job.id)
        self.session.add(self.application)
        self.session.commit()

        for target, replacement in (
            ("app.services.tailoring.analyze_job", lambda job: dict(ANALYSIS)),
            ("app.services.tailoring.select_for_job", fake_plan),
            ("app.services.tailoring.write_cover_letter",
             lambda *a, **k: CoverLetter(
                 body=(
                     "I am applying for the Machine Learning Engineer role at Example "
                     "Labs. I built retrieval pipelines in Python and PyTorch, and "
                     "shipped FastAPI endpoints for model inference. I also built a "
                     "retrieval system with FAISS and evaluated it with RAGAS, which "
                     "is the kind of measurement this posting asks for. " * 3
                 ),
                 unsupported_claims_avoided=[],
             )),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self):
        import asyncio

        return asyncio.run(
            apply_pipeline.build_package(self.session, self.application, self.job)
        )

    def test_there_is_exactly_one_generation_pipeline(self):
        """Two definitions of build_package meant the legacy one silently won."""
        import inspect

        source = inspect.getsource(apply_pipeline)
        self.assertEqual(len(re.findall(r"^async def build_package", source, re.M)), 1)
        self.assertNotIn("tailor_resume", source,
                         "the preserve-everything path must be gone")

    def test_a_profile_with_no_master_resume_still_generates(self):
        package = self.build()
        self.assertTrue(package.ats_score >= 0)
        resume = Path("data/resumes") / Path(package.application_id and
                                             self.application.resume_path).name
        self.assertTrue(resume.is_file(), "the resume PDF must exist on disk")
        self.addCleanup(resume.unlink, True)

    def test_the_profile_path_goes_through_the_same_checks(self):
        package = self.build()
        stored = package.tailored_resume
        self.assertEqual(stored["from_master"], "Your profile")
        self.assertIn("verification", stored)
        self.assertIn("unsupported_requirements", stored)

    def test_an_unsupported_requirement_is_recorded_and_not_written_in(self):
        package = self.build()
        self.assertIn("Kubernetes in production",
                      package.tailored_resume["unsupported_requirements"])
        from app.services import resume_library

        text = resume_library.extract_text(
            Path("backend") / self.application.resume_path
            if Path("backend").is_dir() else Path(self.application.resume_path),
            self.application.resume_path,
        )
        self.assertNotIn("Kubernetes", text)
        self.addCleanup(Path(self.application.resume_path).unlink, True)

    def test_a_fingerprint_is_recorded_so_staleness_can_be_detected(self):
        package = self.build()
        self.assertTrue(package.source_fingerprint)
        self.assertTrue(apply_pipeline.package_is_current(self.session, package, self.job))
        self.addCleanup(Path(self.application.resume_path).unlink, True)

    def test_editing_the_profile_makes_the_stored_package_stale(self):
        package = self.build()
        self.addCleanup(Path(self.application.resume_path).unlink, True)
        row = self.session.get(CandidateProfile, "local")
        data = dict(row.data)
        data["first_name"] = "Changed"
        row.data = data
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(row, "data")
        self.session.commit()
        self.assertFalse(apply_pipeline.package_is_current(self.session, package, self.job))

    def test_a_posting_with_no_description_is_refused_before_any_work(self):
        self.job.description = "See our website for details."
        self.session.commit()
        with self.assertRaises(apply_pipeline.GenerationFailed) as caught:
            self.build()
        # The reason is content-based now, not a word count: this text states
        # no responsibilities or requirements, whatever its length.
        self.assertIn("does not read like a job posting", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

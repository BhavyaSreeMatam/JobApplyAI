"""The four measures, and the rules that keep them apart.

The point of these tests is that no single number can hide a problem: a resume
that parses beautifully must not score well against a job it does not fit, and a
disqualifying eligibility condition must survive as its own finding rather than
being averaged away.
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.candidate import CandidateProfile
from app.services import job_match
from app.services.apply_pipeline import record_adopted_skills
from app.schemas.tailoring import ResumeEntry, TailoredResume
from app.services.tailoring import adoptable_skills, enforce_shape

RESUME = """Test Person
test@example.test | +1 5550001111 | New York, NY

Summary
Machine learning engineer building retrieval systems over internal documents.

Skills
Python, PyTorch, FastAPI, PostgreSQL, Docker

Experience
Machine Learning Engineer | Example Labs | New York, NY | Jun 2021 - Present
- Built retrieval pipelines over internal documents using Python and PyTorch.
- Shipped FastAPI endpoints serving model inference to 40k users.

Education
Master of Science, Computer Science | Example University | 2021
"""

ANALYSIS = {
    "normalized_title": "Machine Learning Engineer",
    "seniority": "Mid-level, 3+ years",
    "keywords": [
        {"term": "Python", "importance": "required"},
        {"term": "PyTorch", "importance": "required"},
        {"term": "Kubernetes", "importance": "required"},
        {"term": "FastAPI", "importance": "preferred"},
        {"term": "Terraform", "importance": "nice_to_have"},
    ],
    "responsibilities": [
        "Build retrieval pipelines over internal documents",
        "Operate Kubernetes clusters for model serving",
    ],
    "eligibility": [
        "Applicants must be authorized to work in the US without sponsorship",
    ],
    "hard_requirements": ["3+ years of experience"],
    "ats_notes": "",
}

PROFILE = {
    "city": "New York",
    "state": "NY",
    "authorized_to_work": True,
    "requires_sponsorship_now": False,
    "requires_sponsorship_future": False,
    "skills": ["Python", "PyTorch", "FastAPI"],
    "experience": [
        {"heading": "Machine Learning Engineer", "organization": "Example Labs",
         "start_year": "2021", "current": True, "bullets": ["Built retrieval pipelines."]},
    ],
    "education": [{"degree": "Master of Science", "major": "Computer Science"}],
}


class MatchComponentTests(unittest.TestCase):
    def test_required_skills_are_scored_on_evidence_not_wishes(self):
        report = job_match.match_report(RESUME, ANALYSIS, PROFILE)
        required = report["components"]["required_skills"]
        self.assertEqual(sorted(required["matched"]), ["PyTorch", "Python"])
        self.assertEqual(required["missing"], ["Kubernetes"])
        self.assertAlmostEqual(required["score"], 66.7, places=1)

    def test_preferred_and_required_are_separate_components(self):
        components = job_match.match_report(RESUME, ANALYSIS, PROFILE)["components"]
        self.assertEqual(components["preferred_qualifications"]["matched"], ["FastAPI"])
        self.assertEqual(components["preferred_qualifications"]["missing"], ["Terraform"])
        self.assertNotIn("FastAPI", components["required_skills"]["matched"])

    def test_responsibility_is_covered_only_when_the_evidence_is_there(self):
        coverage = job_match.responsibility_coverage(
            RESUME, ANALYSIS["responsibilities"]
        )
        covered = [item["responsibility"] for item in coverage["covered"]]
        uncovered = [item["responsibility"] for item in coverage["uncovered"]]
        self.assertIn("Build retrieval pipelines over internal documents", covered)
        self.assertIn("Operate Kubernetes clusters for model serving", uncovered)

    def test_experience_level_uses_the_span_of_real_dates(self):
        component = job_match.experience_component(PROFILE, ANALYSIS)
        self.assertTrue(component["applicable"])
        self.assertEqual(component["required_years"], 3)
        self.assertGreaterEqual(component["candidate_years"], 3)
        self.assertEqual(component["score"], 100.0)

    def test_a_component_with_no_signal_is_dropped_not_scored_zero(self):
        silent = dict(ANALYSIS, responsibilities=[], seniority="", hard_requirements=[],
                      normalized_title="Engineer")
        report = job_match.match_report(RESUME, silent, PROFILE)
        components = report["components"]
        self.assertFalse(components["responsibilities"]["applicable"])
        self.assertFalse(components["experience_level"]["applicable"])
        self.assertEqual(components["responsibilities"]["weight"], 0)
        # The two live components renormalise to 100% between them.
        self.assertEqual(
            components["required_skills"]["weight"]
            + components["preferred_qualifications"]["weight"],
            100,
        )

    def test_undated_experience_does_not_score_zero(self):
        """A blank date field is a gap in the profile, not a junior candidate."""
        component = job_match.experience_component({"experience": []}, ANALYSIS)
        self.assertFalse(component["applicable"])
        self.assertEqual(component["candidate_years"], 0.0)


class EligibilityTests(unittest.TestCase):
    def test_sponsorship_condition_is_met_when_the_profile_says_so(self):
        report = job_match.eligibility_report(PROFILE, ANALYSIS, "New York, NY")
        kinds = {c["kind"]: c["status"] for c in report["conditions"]}
        self.assertEqual(kinds["sponsorship"], "met")
        self.assertEqual(report["blocking"], [])

    def test_needing_sponsorship_blocks_and_is_never_averaged_in(self):
        needs = dict(PROFILE, requires_sponsorship_now=True)
        report = job_match.eligibility_report(needs, ANALYSIS, "New York, NY")
        self.assertEqual([c["kind"] for c in report["blocking"]], ["sponsorship"])

        full = job_match.evaluate(RESUME, ANALYSIS, None, needs, "New York, NY")
        self.assertGreater(full["overall"], 50)  # the resume still matches well
        self.assertTrue(full["issues"][0].startswith("Eligibility:"))

    def test_clearance_is_reported_as_needing_you_never_guessed(self):
        analysis = dict(ANALYSIS, eligibility=["Active TS/SCI security clearance required"])
        report = job_match.eligibility_report(PROFILE, analysis)
        self.assertEqual([c["kind"] for c in report["needs_you"]], ["security clearance"])

    def test_a_higher_degree_satisfies_a_lower_requirement(self):
        analysis = dict(ANALYSIS, eligibility=["Bachelor's degree in Computer Science"])
        report = job_match.eligibility_report(PROFILE, analysis)
        self.assertEqual(report["conditions"][0]["status"], "met")


class CompositionTests(unittest.TestCase):
    def test_parsing_and_quality_are_reported_beside_the_score_not_inside_it(self):
        result = job_match.evaluate(RESUME, ANALYSIS, None, PROFILE, "New York, NY")
        self.assertEqual(result["overall"], result["match"]["score"])
        self.assertIn("format", result)
        self.assertIn("content", result)
        self.assertIn("eligibility", result)

    def test_a_clean_resume_for_the_wrong_job_still_scores_low(self):
        wrong = {
            "normalized_title": "Registered Nurse",
            "seniority": "",
            "keywords": [
                {"term": "phlebotomy", "importance": "required"},
                {"term": "triage", "importance": "required"},
            ],
            "responsibilities": ["Administer medication to admitted patients"],
            "eligibility": [],
            "hard_requirements": [],
            "ats_notes": "",
        }
        result = job_match.evaluate(RESUME, wrong, None, PROFILE)
        self.assertEqual(result["overall"], 0.0)
        # ...while the document itself is perfectly parseable.
        self.assertGreater(result["format"]["score"], 50)

    def test_a_bare_keyword_list_still_works(self):
        result = job_match.evaluate(RESUME, [{"term": "Python", "importance": "required"}])
        self.assertEqual(result["overall"], 100.0)


class AdoptedSkillTests(unittest.TestCase):
    def test_missing_technologies_are_offered_for_adoption(self):
        missing = [
            {"term": "Kubernetes", "importance": "required"},
            {"term": "Terraform", "importance": "nice_to_have"},
        ]
        self.assertEqual(
            adoptable_skills(missing, ["Python"]), ["Kubernetes", "Terraform"]
        )

    def test_a_skill_already_listed_is_not_added_twice(self):
        missing = [{"term": "python", "importance": "required"}]
        self.assertEqual(adoptable_skills(missing, ["Python"]), [])

    def test_requirements_that_are_not_skills_are_never_adopted(self):
        missing = [
            {"term": "5+ years of experience", "importance": "required"},
            {"term": "Bachelor's degree", "importance": "required"},
            {"term": "US citizenship", "importance": "required"},
            {"term": "Active security clearance", "importance": "required"},
            {"term": "excellent communication", "importance": "preferred"},
            {"term": "willing to relocate", "importance": "preferred"},
        ]
        self.assertEqual(adoptable_skills(missing, []), [])

    def test_a_whole_sentence_is_not_a_skill(self):
        missing = [{"term": "experience shipping production machine learning systems at scale",
                    "importance": "required"}]
        self.assertEqual(adoptable_skills(missing, []), [])


class EnforceShapeTests(unittest.TestCase):
    """The resume keeps the profile's shape; only the wording is the model's.

    The candidate asked for exactly this: change the words in the sentences, do
    not change the format. The prompt asks for it and this makes it certain.
    """

    PROFILE = {
        "experience": [
            {"heading": "ML Engineer", "organization": "Example Labs",
             "location": "New York, NY", "start_month": "06", "start_year": "2021",
             "current": True,
             "bullets": ["Built retrieval pipelines.", "Shipped inference endpoints.",
                         "Ran the weekly data review."]},
            {"heading": "Data Analyst", "organization": "Other Co",
             "start_year": "2019", "end_year": "2021",
             "bullets": ["Cleaned survey data."]},
        ],
        "education": [
            {"degree": "Master of Science", "major": "Computer Science",
             "organization": "Example University", "start_year": "2021", "bullets": []},
        ],
    }

    def draft(self, entries):
        return TailoredResume(
            headline="", summary="", skills=[], entries=entries,
            keywords_incorporated=[], keywords_unsupported=[],
        )

    def test_dropped_bullets_come_back_as_originally_written(self):
        """The old prompt told the model to keep 3-5 bullets and drop the rest."""
        resume = self.draft([
            ResumeEntry(section="experience", heading="ML Engineer",
                        organization="Example Labs", dates="", location="",
                        bullets=["Built RAG pipelines over internal documents."]),
        ])
        enforce_shape(resume, self.PROFILE)
        first = resume.section("experience")[0]
        self.assertEqual(len(first.bullets), 3)
        self.assertEqual(first.bullets[0], "Built RAG pipelines over internal documents.")
        self.assertEqual(first.bullets[1], "Shipped inference endpoints.")
        self.assertEqual(first.bullets[2], "Ran the weekly data review.")

    def test_a_dropped_entry_is_restored(self):
        resume = self.draft([
            ResumeEntry(section="experience", heading="ML Engineer",
                        organization="Example Labs", dates="", location="", bullets=[]),
        ])
        enforce_shape(resume, self.PROFILE)
        self.assertEqual(
            [e.heading for e in resume.section("experience")],
            ["ML Engineer", "Data Analyst"],
        )
        self.assertEqual(
            [e.heading for e in resume.section("education")],
            ["Master of Science, Computer Science"],
        )

    def test_headings_and_dates_come_from_the_profile_not_the_model(self):
        resume = self.draft([
            ResumeEntry(section="experience", heading="Senior ML Engineer",
                        organization="Example Laboratories Inc.", dates="2020 - 2024",
                        location="Remote", bullets=["Reworded."]),
        ])
        enforce_shape(resume, self.PROFILE)
        first = resume.section("experience")[0]
        self.assertEqual(first.heading, "ML Engineer")
        self.assertEqual(first.organization, "Example Labs")
        self.assertEqual(first.location, "New York, NY")
        self.assertEqual(first.dates, "Jun 2021 – Present")

    def test_reordered_entries_keep_their_own_rewritten_bullets(self):
        """Matched by name, so a model that reorders cannot cross the wires."""
        resume = self.draft([
            ResumeEntry(section="experience", heading="Data Analyst",
                        organization="Other Co", dates="", location="",
                        bullets=["Cleaned and validated survey data in Python."]),
            ResumeEntry(section="experience", heading="ML Engineer",
                        organization="Example Labs", dates="", location="",
                        bullets=["Built retrieval pipelines in PyTorch.", "b2", "b3"]),
        ])
        enforce_shape(resume, self.PROFILE)
        experience = resume.section("experience")
        self.assertEqual(experience[0].heading, "ML Engineer")
        self.assertEqual(experience[0].bullets[0], "Built retrieval pipelines in PyTorch.")
        self.assertEqual(experience[1].heading, "Data Analyst")
        self.assertEqual(
            experience[1].bullets, ["Cleaned and validated survey data in Python."]
        )


class RecordAdoptedSkillsTests(unittest.TestCase):
    """Adopted skills have to survive into the profile, or the next job re-adds them."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def profile_row(self, data):
        with self.sessions() as session:
            session.add(CandidateProfile(id="local", data=data))
            session.commit()

    def stored(self):
        with self.sessions() as session:
            return session.get(CandidateProfile, "local").data

    def test_new_skills_are_written_to_the_profile_and_tracked(self):
        self.profile_row({"skills": ["Python"]})
        with self.sessions() as session:
            record_adopted_skills(session, ["Kubernetes", "Terraform"])
        data = self.stored()
        self.assertEqual(data["skills"], ["Python", "Kubernetes", "Terraform"])
        self.assertEqual(data["adopted_skills"], ["Kubernetes", "Terraform"])

    def test_a_skill_already_in_the_profile_is_not_duplicated(self):
        self.profile_row({"skills": ["Python", "kubernetes"]})
        with self.sessions() as session:
            record_adopted_skills(session, ["Kubernetes"])
        self.assertEqual(self.stored()["skills"], ["Python", "kubernetes"])

    def test_adoptions_accumulate_across_applications(self):
        self.profile_row({"skills": [], "adopted_skills": ["Ray"]})
        with self.sessions() as session:
            record_adopted_skills(session, ["Terraform"])
        self.assertEqual(self.stored()["adopted_skills"], ["Ray", "Terraform"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

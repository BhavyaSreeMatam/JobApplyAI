"""Selecting from a master resume, and refusing to ship what cannot be supported.

Two guarantees are under test. The first is that a three-page master becomes a
one-page application without the machine deciding to keep everything: lines are
dropped, sections empty out cleanly, and what survives is chosen by how hard it
works rather than by where it sat in the file.

The second is the one that matters more. A job description says what an employer
wants; it is never evidence that this candidate has it. So a re-worded line may
not name a technology the candidate's own material never mentions, a cover letter
may not state a figure the candidate cannot show, and a requirement nobody can
meet is recorded internally rather than written into a document.
"""
import unittest
from types import SimpleNamespace

from app.services import document_check
from app.services.master_resume import Line, MasterResume, Run, classify


def line(index, text, **kwargs):
    runs = kwargs.pop("runs", None) or [Run(text)]
    return Line(index=index, runs=runs, **kwargs)


def resume():
    lines = [
        line(0, "Test Person", runs=[Run("Test Person", bold=True, size=15.0)]),
        line(1, "test@example.test || +1 5550001111"),
        line(2, "SKILLS", runs=[Run("SKILLS", bold=True, underline=True)]),
        line(3, "Languages: Python, Java"),
        line(4, "EXPERIENCE", runs=[Run("EXPERIENCE", bold=True, underline=True)]),
        line(5, "ML Engineer, Example Labs",
             runs=[Run("ML Engineer, Example Labs", bold=True)], right_text="2023 - Now"),
        line(6, "Built retrieval pipelines in Python.", bullet="-"),
        line(7, "Shipped inference endpoints with FastAPI for downstream consumers.",
             bullet="-"),
        line(8, "PUBLICATIONS", runs=[Run("PUBLICATIONS", bold=True, underline=True)]),
        line(9, "A paper about something unrelated.", bullet="-"),
    ]
    classify(lines)
    return MasterResume("x.pdf", lines, {}, "pdf")


class SelectionTests(unittest.TestCase):
    def test_a_plan_keeps_compresses_and_removes(self):
        document = resume()
        counts = document.apply_plan([
            {"index": 6, "action": "keep", "text": "", "priority": 1},
            {"index": 7, "action": "compress", "text": "Shipped FastAPI inference endpoints.",
             "priority": 3},
            {"index": 9, "action": "remove", "text": "", "priority": 5},
        ])
        self.assertEqual(counts["kept"], 1)
        self.assertEqual(counts["compressed"], 1)
        self.assertEqual(counts["removed"], 1)
        self.assertEqual(document.lines[7].text, "Shipped FastAPI inference endpoints.")
        self.assertTrue(document.lines[9].removed)

    def test_an_emptied_section_takes_its_heading_with_it(self):
        """A section title above blank space is how a machine-cut resume looks."""
        document = resume()
        document.apply_plan([{"index": 9, "action": "remove", "text": "", "priority": 5}])
        self.assertTrue(document.lines[8].removed, "PUBLICATIONS heading should go too")
        self.assertFalse(document.lines[4].removed, "EXPERIENCE still has content")
        self.assertNotIn("PUBLICATIONS", document.plain_text())

    def test_structural_lines_cannot_be_removed(self):
        document = resume()
        counts = document.apply_plan([
            {"index": 0, "action": "remove", "text": "", "priority": 5},
            {"index": 1, "action": "remove", "text": "", "priority": 5},
            {"index": 2, "action": "remove", "text": "", "priority": 5},
        ])
        self.assertEqual(counts["refused"], 3)
        self.assertIn("Test Person", document.plain_text())
        self.assertIn("test@example.test", document.plain_text())

    def test_an_entry_is_never_reworded(self):
        """Employer, title and dates are facts of record, not wording."""
        document = resume()
        document.apply_plan([
            {"index": 5, "action": "compress", "text": "Senior ML Engineer, Example Laboratories",
             "priority": 1},
        ])
        self.assertEqual(document.lines[5].text, "ML Engineer, Example Labs")
        self.assertEqual(document.lines[5].right_text, "2023 - Now")

    def test_a_line_the_plan_forgot_is_kept(self):
        """Silence leaves the resume longer, never emptier."""
        document = resume()
        document.apply_plan([])
        self.assertEqual(len(document.live), len(document.lines))

    def test_removed_content_leaves_the_rendered_text(self):
        document = resume()
        document.apply_plan([{"index": 7, "action": "remove", "text": "", "priority": 5}])
        self.assertNotIn("inference endpoints", document.plain_text())


class TrimmingTests(unittest.TestCase):
    def test_the_weakest_bullets_go_first(self):
        document = resume()
        document.apply_plan([
            {"index": 6, "action": "keep", "text": "", "priority": 1},
            {"index": 7, "action": "keep", "text": "", "priority": 4},
            {"index": 9, "action": "keep", "text": "", "priority": 5},
        ])
        self.assertEqual(document.trim_to_fit(1), [9])

    def test_priority_one_evidence_is_never_trimmed(self):
        document = resume()
        document.apply_plan([
            {"index": 6, "action": "keep", "text": "", "priority": 1},
            {"index": 7, "action": "remove", "text": "", "priority": 5},
            {"index": 9, "action": "remove", "text": "", "priority": 5},
        ])
        self.assertEqual(document.trim_to_fit(5), [])
        self.assertIn("retrieval pipelines", document.plain_text())

    def test_the_strongest_role_keeps_at_least_one_bullet(self):
        """A job the application turns on is not reduced to a bare line."""
        document = resume()
        document.apply_plan([
            {"index": 5, "action": "keep", "text": "", "priority": 1},
            {"index": 6, "action": "keep", "text": "", "priority": 3},
            {"index": 7, "action": "remove", "text": "", "priority": 5},
        ])
        document.trim_to_fit(3)
        self.assertIn("retrieval pipelines", document.plain_text())


class GroundingTests(unittest.TestCase):
    MASTER = (
        "Implemented CNN, RNN and Transformer models using TensorFlow, PyTorch and "
        "scikit-learn, improving accuracy by up to 15%."
    )

    def verified(self):
        return document_check._vocabulary(self.MASTER)

    def edited(self, new_text):
        document = resume()
        document.lines[6].original = [Run(self.MASTER)]
        document.lines[6].runs = [Run(new_text)]
        return document

    def test_a_technology_the_candidate_never_named_is_caught(self):
        failures = document_check.ground_edits(
            self.edited("Implemented CNN models and deployed them on Kubernetes."),
            self.verified(),
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("kubernetes", failures[0]["detail"])

    def test_a_metric_that_was_not_in_the_source_is_caught(self):
        failures = document_check.ground_edits(
            self.edited("Implemented CNN models, improving accuracy by up to 40%."),
            self.verified(),
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("number", failures[0]["problem"])

    def test_rewording_the_same_facts_passes(self):
        failures = document_check.ground_edits(
            self.edited(
                "Benchmarked CNN, RNN and Transformer models in PyTorch and "
                "TensorFlow, improving accuracy by up to 15%."
            ),
            self.verified(),
        )
        self.assertEqual(failures, [])

    def test_a_slash_joined_list_is_checked_part_by_part(self):
        """"PyTorch/TensorFlow" is one token to a tokeniser and two claims to a reader."""
        verified = self.verified()
        self.assertTrue(document_check._supported("pytorch/tensorflow", verified))
        self.assertTrue(document_check._supported("cnn/rnn/transformer", verified))
        self.assertFalse(document_check._supported("pytorch/kubernetes", verified))

    def test_contact_details_are_not_read_as_claims(self):
        self.assertEqual(
            document_check._numbers("test@example.test | +1 5550001111 | New York"), set()
        )
        self.assertEqual(
            document_check._numbers("cut runtime 30% across 3 services"), {"30%", "3"}
        )


class CoverLetterTests(unittest.TestCase):
    JOB = SimpleNamespace(
        title="Machine Learning Engineer - 2027 Start",
        company="Example Labs Inc.",
        description="Build ranking models. Run A/B tests.",
    )
    RESUME = "Built retrieval pipelines in Python. Improved accuracy by 15%."
    PROFILE = {"first_name": "Test", "skills": ["Python"]}

    def check(self, body, pages=1):
        return document_check.check_cover_letter(
            body, body, pages, self.JOB, self.RESUME, self.PROFILE, self.RESUME
        )

    def good(self):
        return (
            "I am applying for the Machine Learning Engineer role at Example Labs. "
            + "I built retrieval pipelines in Python and improved accuracy by 15 percent "
              "on a labelled evaluation set, comparing configurations rather than guessing. " * 12
        )

    def test_a_sound_letter_passes(self):
        self.assertEqual(self.check(self.good()), [])

    def test_an_invented_figure_is_caught(self):
        problems = self.check(self.good() + " I cut latency by 80%.")
        self.assertTrue(any("figures" in p for p in problems), problems)

    def test_the_posting_s_own_year_is_not_an_invented_date(self):
        """"2027 Start" is the employer's date, not a claim about the candidate."""
        self.assertEqual(self.check(self.good() + " I can start in 2027."), [])

    def test_a_year_from_nowhere_is_caught(self):
        problems = self.check(self.good() + " I worked there from 1998.")
        self.assertTrue(any("dates" in p for p in problems), problems)

    def test_a_placeholder_is_caught(self):
        problems = self.check(self.good() + " I admire [Company].")
        self.assertTrue(any("placeholder" in p for p in problems), problems)

    def test_the_legal_suffix_is_not_required_in_the_body(self):
        """A letter naming "Example Labs" has named "Example Labs Inc.""" ""
        self.assertNotIn(
            "never names the company", " ".join(self.check(self.good()))
        )

    def test_a_letter_that_never_names_the_company_is_caught(self):
        body = "I am applying for this role. " * 40
        self.assertTrue(
            any("never names the company" in p for p in self.check(body))
        )

    def test_length_and_page_limits_are_enforced(self):
        self.assertTrue(any("too thin" in p for p in self.check("Too short.")))
        self.assertTrue(any("one page" in p for p in self.check(self.good() * 3)))
        self.assertTrue(any("2 pages" in p for p in self.check(self.good(), pages=2)))


class RenderedResumeTests(unittest.TestCase):
    PROFILE = {"email": "test@example.test", "phone": "+1 5550001111"}

    def body(self):
        return (
            "Test Person\ntest@example.test | +1 5550001111\n"
            + "Built retrieval pipelines in Python and shipped FastAPI endpoints. " * 8
        )

    def test_a_sound_resume_passes(self):
        self.assertEqual(
            document_check.check_resume(resume(), self.body(), 1, 1, self.PROFILE), []
        )

    def test_running_over_the_page_target_is_caught(self):
        problems = document_check.check_resume(resume(), self.body(), 2, 1, self.PROFILE)
        self.assertTrue(any("against a target" in p for p in problems))

    def test_missing_contact_details_are_caught(self):
        problems = document_check.check_resume(
            resume(), "Test Person\n" + "words " * 80, 1, 1, self.PROFILE
        )
        self.assertTrue(any("email" in p for p in problems), problems)

    def test_unmapped_glyphs_are_caught(self):
        problems = document_check.check_resume(
            resume(), self.body() + " (cid:127)", 1, 1, self.PROFILE
        )
        self.assertTrue(any("glyph" in p for p in problems))

    def test_content_that_was_removed_must_actually_be_gone(self):
        document = resume()
        document.apply_plan([{"index": 7, "action": "remove", "text": "", "priority": 5}])
        stale = self.body() + " Shipped inference endpoints with FastAPI for downstream consumers."
        problems = document_check.check_resume(document, stale, 1, 1, self.PROFILE)
        self.assertTrue(any("still in the PDF" in p for p in problems), problems)


class ConfirmedSkillTests(unittest.TestCase):
    """A skill the machine added yesterday must not authorise itself today."""

    def missing(self, *terms):
        return [{"term": term, "importance": "required"} for term in terms]

    def test_only_skills_the_candidate_typed_bridge_a_gap(self):
        from app.services.apply_pipeline import _confirmed_gap_skills

        profile = {
            "skills": ["Python", "Spark", "A/B Testing"],
            "adopted_skills": ["A/B Testing"],
        }
        adopted = _confirmed_gap_skills(
            self.missing("Spark", "A/B Testing", "Kubernetes"), profile, resume()
        )
        self.assertEqual(adopted, ["Spark"])

    def test_a_skill_already_on_the_resume_is_not_re_added(self):
        from app.services.apply_pipeline import _confirmed_gap_skills

        profile = {"skills": ["Python"], "adopted_skills": []}
        self.assertEqual(
            _confirmed_gap_skills(self.missing("Python"), profile, resume()), []
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

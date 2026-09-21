"""What must survive trimming, and where the page comes from instead.

These lock in a failure rather than a feature. Trimming by priority alone, against
a search-ranking posting, deleted the candidate's first-author publication and
their teaching section and kept seven lines of skills list: a page of the right
length making the weakest possible case.

So: evidence that answers a stated requirement, or that takes years to earn and
one line to delete, is protected. The space comes out of the skills block, which
is a list of nouns with no evidence attached and the cheapest content on the page.
A project heading whose bullets all went is removed with them, and a resume that
can only reach one page by cutting protected evidence is allowed to run to two.
"""
import unittest

from app.services import evidence
from app.services.master_resume import Line, MasterResume, Run, classify


def line(index, text, **kwargs):
    runs = kwargs.pop("runs", None) or [Run(text)]
    return Line(index=index, runs=runs, **kwargs)


ANALYSIS = {
    "keywords": [
        {"term": "PyTorch", "importance": "required"},
        {"term": "evaluation", "importance": "required"},
        {"term": "Java", "importance": "nice_to_have"},
    ],
    "responsibilities": ["Evaluate models against real measurements"],
    "hard_requirements": [],
    "outcomes": [],
    "normalized_title": "ML Engineer",
}


def document():
    lines = [
        line(0, "SKILLS", runs=[Run("SKILLS", bold=True, underline=True)]),
        line(1, "Languages: Python, Java, COBOL, Fortran, Pascal, Delphi"),
        line(2, "PUBLICATIONS", runs=[Run("PUBLICATIONS", bold=True, underline=True)]),
        line(3, "Morgan, A. (2025). A paper at IEEE ISMAR. First author.", bullet="-"),
        line(4, "TEACHING", runs=[Run("TEACHING", bold=True, underline=True)]),
        line(5, "Mentored graduate students building immersive applications.", bullet="-"),
        line(6, "EXPERIENCE", runs=[Run("EXPERIENCE", bold=True, underline=True)]),
        line(7, "Engineer, Example Labs", runs=[Run("Engineer, Example Labs", bold=True)]),
        line(8, "Benchmarked models in PyTorch with offline evaluation.", bullet="-"),
        line(9, "Attended the weekly team meeting.", bullet="-"),
    ]
    classify(lines)
    resume = MasterResume("x.pdf", lines, {}, "pdf")
    resume.apply_plan([
        {"index": index, "action": "keep", "text": "", "priority": 3}
        for index in range(1, 10)
    ])
    return resume


class ProtectionTests(unittest.TestCase):
    def test_strong_evidence_is_protected(self):
        protect = evidence.protected(document(), ANALYSIS)
        self.assertIn(3, protect, "the publication must be protected")
        self.assertIn(5, protect, "mentoring must be protected")
        self.assertIn(8, protect, "model evaluation must be protected")

    def test_a_line_with_nothing_to_prove_is_not_protected(self):
        self.assertNotIn(9, evidence.protected(document(), ANALYSIS))

    def test_trimming_takes_the_weak_bullet_and_leaves_the_publication(self):
        resume = document()
        dropped = resume.trim_to_fit(5, evidence.protected(resume, ANALYSIS))
        self.assertEqual(dropped, [9])
        text = resume.plain_text()
        self.assertIn("ISMAR", text)
        self.assertIn("Mentored", text)
        self.assertIn("Benchmarked", text)

    def test_the_last_evidence_for_a_requirement_comes_back(self):
        resume = document()
        before = evidence.coverage(resume, ANALYSIS)
        resume.lines[8].removed = True  # forced, bypassing protection
        lost = evidence.lost_requirements(resume, ANALYSIS, before)
        self.assertIn(8, lost)
        resume.restore(8)
        self.assertIn("Benchmarked", resume.plain_text())

    def test_each_strong_kind_is_recognised(self):
        for text, kind in (
            ("Published at IEEE ISMAR, first author.", "publication"),
            ("Mentored three graduate students.", "mentoring"),
            ("Deployed the service on Amazon EC2 with Docker.", "deployment"),
            ("Benchmarked retrieval quality against a labelled set.", "evaluation"),
            ("Trained an LSTM classifier in PyTorch.", "model_work"),
        ):
            with self.subTest(kind=kind):
                self.assertIn(kind, evidence.strong_kinds(text))
        self.assertEqual(evidence.strong_kinds("Attended the weekly team meeting."), set())


class SkillsCompressionTests(unittest.TestCase):
    def test_the_skills_list_is_where_the_space_comes_from(self):
        resume = document()
        dropped = resume.compress_skills(evidence.job_vocabulary(ANALYSIS))
        self.assertGreater(dropped, 0)
        skills = resume.lines[1].text
        self.assertIn("Python", skills)
        self.assertIn("Java", skills)
        self.assertNotIn("Delphi", skills)

    def test_compression_never_welds_two_terms_together(self):
        """PyTorch, followed by GANs in the next run, came out as PyTorchGANs."""
        lines = [
            line(0, "SKILLS", runs=[Run("SKILLS", bold=True, underline=True)]),
            line(1, "x", runs=[
                Run("ML: ", bold=True),
                Run("PyTorch, Keras, Delphi, Fortran, Pascal, COBOL, "),
                Run("GANs"),
            ]),
        ]
        classify(lines)
        resume = MasterResume("x.pdf", lines, {}, "pdf")
        resume.compress_skills({"pytorch", "gans"}, keep_at_least=1)
        text = resume.lines[1].text
        self.assertNotIn("PyTorchGANs", text)
        self.assertIn("PyTorch", text)
        self.assertIn("GANs", text)

    def test_a_category_keeps_a_few_terms_whatever_the_job_asks(self):
        resume = document()
        resume.compress_skills(set(), keep_at_least=3)
        self.assertGreaterEqual(len(resume.lines[1].text.split(",")), 3)


class EmptyHeadingTests(unittest.TestCase):
    def document(self):
        lines = [
            line(0, "EXPERIENCE", runs=[Run("EXPERIENCE", bold=True, underline=True)]),
            line(1, "Engineer, Example Labs", runs=[Run("Engineer, Example Labs", bold=True)]),
            line(2, "Built retrieval pipelines in Python.", bullet="-"),
            line(3, "PROJECTS", runs=[Run("PROJECTS", bold=True, underline=True)]),
            line(4, "Some Side Project", runs=[Run("Some Side Project", bold=True)]),
            line(5, "A bullet about the side project.", bullet="-"),
            line(6, "Another Project", runs=[Run("Another Project", bold=True)]),
            line(7, "A bullet about the other project.", bullet="-"),
        ]
        classify(lines)
        return MasterResume("x.pdf", lines, {}, "pdf")

    def test_a_project_with_no_bullets_left_is_removed(self):
        """A project title carrying nothing is noise, not evidence."""
        resume = self.document()
        resume.apply_plan([{"index": 5, "action": "remove", "text": "", "priority": 5}])
        self.assertTrue(resume.lines[4].removed)
        self.assertNotIn("Some Side Project", resume.plain_text())
        self.assertIn("Another Project", resume.plain_text())

    def test_a_job_with_no_bullets_left_keeps_its_place(self):
        """An employer and its dates are the career history, bullets or not."""
        resume = self.document()
        resume.apply_plan([{"index": 2, "action": "remove", "text": "", "priority": 5}])
        self.assertFalse(resume.lines[1].removed)
        self.assertIn("Engineer, Example Labs", resume.plain_text())

    def test_emptying_every_project_removes_the_section(self):
        resume = self.document()
        resume.apply_plan([
            {"index": 5, "action": "remove", "text": "", "priority": 5},
            {"index": 7, "action": "remove", "text": "", "priority": 5},
        ])
        self.assertNotIn("PROJECTS", resume.plain_text())


class SpacingTests(unittest.TestCase):
    def document(self):
        lines = [
            line(0, "EXPERIENCE", runs=[Run("EXPERIENCE", bold=True, underline=True)],
                 space_before=12.0),
            line(1, "Engineer, A", runs=[Run("Engineer, A", bold=True)], space_before=2.0),
            line(2, "First bullet about the work.", bullet="-", space_before=0.0),
            line(3, "Second bullet about the work.", bullet="-", space_before=0.0),
            line(4, "Engineer, B", runs=[Run("Engineer, B", bold=True)], space_before=11.0),
            line(5, "Third bullet about the work.", bullet="-", space_before=0.0),
        ]
        classify(lines)
        return MasterResume("x.pdf", lines, {}, "pdf")

    def test_nothing_removed_means_nothing_respaced(self):
        """A resume with no cuts has to render exactly as it was parsed."""
        resume = self.document()
        before = [item.space_before for item in resume.lines]
        resume.resettle_spacing()
        self.assertEqual([item.space_before for item in resume.lines], before)

    def test_a_gap_is_rebuilt_from_what_now_sits_above(self):
        """An entry under a heading needs the heading's gap, not another entry's.

        Using one median per kind instead cost a real resume 160 points of white
        space - about twelve lines, more than the trimming was recovering.
        """
        resume = self.document()
        for index in (1, 2, 3):
            resume.lines[index].removed = True
        resume.resettle_spacing()
        self.assertEqual(resume.lines[4].space_before, 2.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

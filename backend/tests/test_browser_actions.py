"""The one rule this application cannot get wrong: it never submits anything.

The rule used to live in several lists at once - the autofiller's, the question
collector's, the upload revealer's, the Workday adapter's - and one of those
lists named "Submit application form" as a control to press in order to *open* an
application. These tests exist so that cannot come back.
"""
import unittest

from app.services import browser_actions as actions


class SubmitTests(unittest.TestCase):
    SUBMITS = [
        "Submit",
        "Submit application",
        "Submit application form",
        "Submit your application",
        "Send application",
        "Review and submit",
        "Confirm and submit",
        "Complete application",
        "Finish and apply",
        "Finalize application",
        "Apply and submit",
        "SUBMIT APPLICATION",
        "  Submit  ",
    ]

    def test_every_submission_control_is_recognised(self):
        for label in self.SUBMITS:
            with self.subTest(label=label):
                self.assertEqual(actions.classify(label), actions.SUBMIT)
                self.assertTrue(actions.is_submit(label))

    def test_nothing_may_ever_click_a_submit(self):
        """No caller, for any purpose, gets permission."""
        for label in self.SUBMITS:
            for purpose in (actions.FOR_OPENING, actions.FOR_NAVIGATION,
                            {actions.OPEN, actions.NAVIGATE, actions.SAVE, actions.UNKNOWN}):
                with self.subTest(label=label, purpose=sorted(purpose)):
                    self.assertFalse(actions.may_click(label, purpose))

    def test_the_refusal_says_what_to_do(self):
        self.assertIn("press it yourself", actions.refusal("Submit application"))


class DeclarationTests(unittest.TestCase):
    def test_declarations_are_never_pressed(self):
        for label in ("I agree", "I certify that the above is true", "I accept the terms",
                      "Agree and continue", "I consent", "E-Sign", "I acknowledge"):
            with self.subTest(label=label):
                self.assertFalse(actions.may_click(label, actions.FOR_NAVIGATION))
                self.assertIn("only you", actions.refusal(label))


class OpeningTests(unittest.TestCase):
    def test_real_apply_controls_are_openable(self):
        for label in ("Apply", "Apply now", "Apply for this job", "Easy Apply",
                      "I'm interested", "Start application", "Apply on company site"):
            with self.subTest(label=label):
                self.assertEqual(actions.classify(label), actions.OPEN)
                self.assertTrue(actions.may_click(label, actions.FOR_OPENING))

    def test_a_bare_apply_substring_does_not_make_a_control_safe(self):
        """"Apply" is inside "Submit application" and inside "Reapply"."""
        for label in ("Submit application", "Reapply to this job",
                      "Apply and submit", "Applying disqualifies you"):
            with self.subTest(label=label):
                self.assertFalse(actions.may_click(label, actions.FOR_OPENING))

    def test_an_open_control_is_not_treated_as_navigation(self):
        self.assertFalse(actions.may_click("Apply now", actions.FOR_NAVIGATION))


class NavigationTests(unittest.TestCase):
    def test_step_controls_are_navigable(self):
        for label in ("Next", "Continue", "Save and continue", "Next step",
                      "Proceed", "Back", "Continue >"):
            with self.subTest(label=label):
                self.assertTrue(actions.may_click(label, actions.FOR_NAVIGATION))

    def test_saving_a_draft_is_allowed(self):
        for label in ("Save", "Save draft", "Save for later", "Save my progress"):
            with self.subTest(label=label):
                self.assertEqual(actions.classify(label), actions.SAVE)
                self.assertTrue(actions.may_click(label, actions.FOR_NAVIGATION))

    def test_review_and_submit_is_a_submit_not_a_step(self):
        """It reads like navigation and is the last thing anyone wants clicked."""
        self.assertEqual(actions.classify("Review and submit"), actions.SUBMIT)
        self.assertEqual(actions.classify("Review"), actions.NAVIGATE)


class UnknownTests(unittest.TestCase):
    def test_an_unrecognised_control_is_left_alone(self):
        for label in ("Withdraw", "Delete my data", "Download PDF", "", "   ",
                      "Request accommodation"):
            with self.subTest(label=label):
                self.assertEqual(actions.classify(label), actions.UNKNOWN)
                self.assertFalse(actions.may_click(label, actions.FOR_NAVIGATION))
                self.assertFalse(actions.may_click(label, actions.FOR_OPENING))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

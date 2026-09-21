"""Attaching the tailored resume when the upload control is not on the page yet.

Indeed's review step shows the resume already on your account behind an "Edit"
link and renders no file input at all, so the filler found nothing, said nothing
useful, and the employer received whichever resume the site had on file - which
is the one thing tailoring exists to avoid.

These run against a local page that reproduces that shape, including the two
traps: a second identical "Edit" belonging to a different section, and a Submit
button that must never be touched while hunting for an upload.
"""
import unittest
from pathlib import Path

from app.services import autofill

PAGE = """
<div class="card" id="contactcard">
  <div class="row"><h3>Contact</h3><button id="e1">Edit</button></div>
  <p>+1 555 000 1111</p>
</div>
<div class="card" id="resumecard">
  <div class="row"><h3>Resume</h3>
    <span><a href="#" id="dl">Download</a> <button id="editresume">Edit</button></span>
  </div>
  <p>Existing_Resume.pdf</p>
</div>
<div class="card" id="covercard">
  <div class="row"><h3>Cover letter</h3><button id="e3">Edit</button></div>
  <p>Not included</p>
</div>
<button id="submit">Submit your application</button>
<script>
  document.getElementById('editresume').onclick = () => {
    document.getElementById('resumecard').innerHTML =
      '<h3>Resume</h3><label>Upload a different file' +
      '<input type="file" name="resumeUpload"></label>';
  };
  document.getElementById('e3').onclick = () => {
    document.getElementById('covercard').innerHTML =
      '<h3>Cover letter</h3><label>Attach a cover letter' +
      '<input type="file" name="coverUpload"></label>';
  };
  document.getElementById('e1').onclick = () => { document.body.dataset.wrong = 'contact'; };
  document.getElementById('submit').onclick = () => { document.body.dataset.submitted = 'yes'; };
</script>
"""


class UploadRevealTests(unittest.TestCase):
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
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.resume = Path(self.temp.name) / "Tailored_Resume.pdf"
        self.resume.write_bytes(b"%PDF-1.4 tailored")
        self.cover = Path(self.temp.name) / "Tailored_Cover.pdf"
        self.cover.write_bytes(b"%PDF-1.4 cover")

        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.set_content(PAGE)

    def attached(self, name):
        return self.page.evaluate(
            """(name) => {
                const input = document.querySelector(`input[name=${name}]`);
                return input && input.files[0] ? input.files[0].name : null;
            }""",
            name,
        )

    def test_the_resume_upload_is_opened_and_the_tailored_file_attached(self):
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        self.assertEqual(skipped, [])
        self.assertEqual(len(filled), 1)
        self.assertIn("Tailored_Resume.pdf", filled[0]["value"])
        self.assertEqual(self.attached("resumeUpload"), "Tailored_Resume.pdf")

    def test_the_identical_edit_on_another_section_is_never_clicked(self):
        """Contact and Resume offer the same control; only the block differs."""
        autofill._attach_document(self.page, "resume", self.resume, [], [])
        self.assertEqual(self.page.evaluate("() => document.body.dataset.wrong || 'no'"), "no")

    def test_nothing_is_ever_submitted_while_looking_for_an_upload(self):
        autofill._attach_document(self.page, "resume", self.resume, [], [])
        autofill._attach_document(self.page, "cover letter", self.cover, [], [])
        self.assertEqual(
            self.page.evaluate("() => document.body.dataset.submitted || 'no'"), "no"
        )

    def test_a_revealed_control_is_found_by_being_new_not_by_its_name(self):
        """Indeed names it "coverUpload" - no "letter" anywhere in it."""
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        autofill._attach_document(self.page, "cover letter", self.cover, filled, skipped)
        self.assertEqual(skipped, [])
        self.assertEqual(self.attached("coverUpload"), "Tailored_Cover.pdf")
        # ...and the resume's own control was not reused for the cover letter.
        self.assertEqual(self.attached("resumeUpload"), "Tailored_Resume.pdf")

    def test_a_page_with_no_upload_anywhere_says_so_plainly(self):
        self.page.set_content("<p>Thanks for applying.</p>")
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        self.assertEqual(filled, [])
        self.assertIn("no upload control", skipped[0]["reason"])

    def test_a_plain_upload_form_still_works_without_any_clicking(self):
        self.page.set_content('<label>Resume<input type="file" name="resume"></label>')
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        self.assertEqual(skipped, [])
        self.assertEqual(self.attached("resume"), "Tailored_Resume.pdf")
        self.assertNotIn("opened via", filled[0]["value"])


class UploadAcceptanceTests(UploadRevealTests):
    """Selecting a local file is not the employer accepting it."""

    def test_a_rejected_file_is_not_reported_as_uploaded(self):
        self.page.set_content(
            '<label>Resume<input type="file" name="resume"></label>'
            '<p id="msg"></p>'
            '<script>document.querySelector("input").onchange = () => {'
            '  document.getElementById("msg").textContent ='
            '    "Upload failed: file too large. Only PDF under 2MB is accepted.";'
            '};</script>'
        )
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        self.assertEqual(filled, [], "a rejected upload must never count as filled")
        self.assertIn("reported a problem", skipped[0]["reason"])

    def test_a_stale_attachment_is_detected(self):
        """The control still holding an older document is a failure, not a pass."""
        self.page.set_content(
            '<label>Resume<input type="file" name="resume"></label>'
            '<script>document.querySelector("input").addEventListener("change", (e) => {'
            '  const dt = new DataTransfer();'
            '  dt.items.add(new File(["old"], "Old_Resume.pdf", {type: "application/pdf"}));'
            '  e.target.files = dt.files;'
            '});</script>'
        )
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        self.assertEqual(filled, [])
        self.assertIn("Old_Resume.pdf", skipped[0]["reason"])

    def test_an_accepted_file_is_reported_as_uploaded(self):
        self.page.set_content('<label>Resume<input type="file" name="resume"></label>')
        filled, skipped = [], []
        autofill._attach_document(self.page, "resume", self.resume, filled, skipped)
        self.assertEqual(skipped, [])
        self.assertIn("Tailored_Resume.pdf", filled[0]["value"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

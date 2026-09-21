"""Regression checks for importing a draft and reviewing suggested skills.

Uses a temporary database and mocked parsing; never calls a paid AI service.
"""
import copy
import json
import unittest
from unittest.mock import patch

from app.models.candidate import CandidateProfile
from app.services import apply_pipeline, document_check, master_resume, profile_merge, workday
from tests import test_resume_import as fixtures


class DraftImportTests(unittest.TestCase):
    setUp = fixtures.ImportEndpointTests.setUp
    seed = fixtures.ImportEndpointTests.seed

    def upload(self, draft, revision=7):
        return self.client.post(
            "/config/import-resume",
            data={"profile_json": json.dumps(draft), "revision": str(revision)},
            files={"resume": ("resume.txt", fixtures.RESUME_TEXT.encode(), "text/plain")},
        )

    def test_unsaved_edits_survive_import_save_and_reload(self):
        self.seed({"first_name": "Test", "location": "Saved city"})
        draft = {
            "first_name": "Test", "location": "Unsaved city", "relocation": False,
            "skills": ["SQL", "Kubernetes"], "adopted_skills": ["Kubernetes"],
            "experience": [
                {"heading": "ML Engineer", "organization": "Example Labs",
                 "location": "Remote", "current": True,
                 "bullets": ["A manually entered achievement."]},
                {"heading": "Intern", "organization": "Other employer",
                 "bullets": ["A job absent from this resume."]},
            ],
        }
        response = self.upload(draft)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        merged = body["merged_preview"]
        self.assertEqual(merged["location"], "Unsaved city")
        self.assertIs(merged["relocation"], False)
        self.assertEqual(len(merged["experience"]), 2)
        self.assertTrue(merged["experience"][0]["current"])
        self.assertEqual(merged["experience"][0]["location"], "Remote")
        self.assertIn("A manually entered achievement.", merged["experience"][0]["bullets"])
        self.assertIn("Built retrieval pipelines in Python.", merged["experience"][0]["bullets"])
        self.assertEqual(merged["skills"], ["SQL", "Kubernetes", "Python", "PyTorch"])
        # Import is a preview, so closing the page cannot silently save the draft.
        saved = self.client.get("/agent/profile").json()
        self.assertEqual(saved["data"]["location"], "Saved city")
        self.assertEqual(saved["revision"], 7)
        response = self.client.put("/agent/profile", json={
            "revision": body["current_revision"], "data": merged,
        })
        self.assertEqual(response.status_code, 200, response.text)
        reloaded = self.client.get("/agent/profile").json()
        self.assertEqual(reloaded["data"]["location"], "Unsaved city")
        self.assertEqual(reloaded["data"]["provenance"]["skills"]["python"], "document")
        self.assertEqual(profile_merge.unconfirmed_skills(reloaded["data"]), ["Kubernetes"])

    def test_stale_draft_is_rejected_before_paid_parsing(self):
        self.seed({"first_name": "Test"})
        with patch("app.api.config.tailoring.parse_resume_text") as parse:
            response = self.upload({"first_name": "Unsaved"}, revision=6)
        self.assertEqual(response.status_code, 409, response.text)
        parse.assert_not_called()

    def test_invalid_draft_is_rejected_before_paid_parsing(self):
        self.seed({"first_name": "Test"})
        with patch("app.api.config.tailoring.parse_resume_text") as parse:
            response = self.upload({"skills": "not a list"})
        self.assertEqual(response.status_code, 422, response.text)
        parse.assert_not_called()

    def test_revision_and_draft_must_arrive_together(self):
        response = self.client.post(
            "/config/import-resume", data={"profile_json": "{}"},
            files={"resume": ("resume.txt", fixtures.RESUME_TEXT.encode(), "text/plain")},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_change_during_parsing_cannot_relabel_a_stale_preview(self):
        self.seed({"first_name": "Test"})

        def parse(_text):
            with self.sessions() as session:
                row = session.get(CandidateProfile, "local")
                row.data = {"first_name": "Saved elsewhere"}
                row.revision = 8
                session.commit()
            return fixtures.parsed_profile()

        with patch("app.api.config.tailoring.parse_resume_text", side_effect=parse):
            response = self.upload({"first_name": "Unsaved"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/agent/profile").json()["data"]["first_name"], "Saved elsewhere")

    def test_confirmation_survives_an_ordinary_profile_save(self):
        self.seed({"skills": ["Python", "Kubernetes"], "adopted_skills": ["Kubernetes"]})
        response = self.client.post("/agent/profile/confirm-skills", json={"skills": ["Kubernetes"]})
        self.assertEqual(response.status_code, 200, response.text)
        current = self.client.get("/agent/profile").json()
        current["data"]["location"] = "Another edit"
        saved = self.client.put("/agent/profile", json=current)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.get("/agent/profile/pending-skills").json()["pending"], [])
        self.assertEqual(saved.json()["data"]["provenance"]["skills"]["kubernetes"], "user")

    def test_generation_profile_filters_pending_without_changing_storage(self):
        data = {
            "first_name": "Test", "last_name": "Person", "email": "test@example.test",
            "projects": [{"heading": "Demo", "bullets": ["Built a Python tool."]}],
            "skills": ["Python", "Kubernetes"], "adopted_skills": ["Kubernetes"],
        }
        self.seed(data)
        with self.sessions() as session:
            profile = apply_pipeline.load_profile(session)
            self.assertEqual(profile["skills"], ["Python"])
            self.assertNotIn("Kubernetes", json.dumps(profile))
            self.assertEqual(session.get(CandidateProfile, "local").data, data)

    def test_new_suggestions_invalidate_an_old_profile_revision(self):
        self.seed({"skills": ["Python"]})
        with self.sessions() as session:
            apply_pipeline.record_adopted_skills(session, ["Kubernetes"])
        current = self.client.get("/agent/profile").json()
        self.assertEqual(current["revision"], 8)
        self.assertEqual(profile_merge.unconfirmed_skills(current["data"]), ["Kubernetes"])
        stale = self.client.put("/agent/profile", json={"revision": 7, "data": {"skills": ["Python"]}})
        self.assertEqual(stale.status_code, 409, stale.text)


class SkillEvidenceTests(unittest.TestCase):
    def test_reimport_does_not_promote_an_unrelated_machine_skill(self):
        merged, _ = profile_merge.merge_profile({
            "skills": ["Kubernetes"], "provenance": {"skills": {"kubernetes": "machine"}},
        }, {"skills": ["Python"]})
        self.assertEqual(profile_merge.unconfirmed_skills(merged), ["Kubernetes"])

    def test_imported_evidence_can_confirm_a_previously_adopted_skill(self):
        merged, _ = profile_merge.merge_profile({
            "skills": ["Kubernetes"], "adopted_skills": ["Kubernetes"],
        }, {"skills": ["Kubernetes"]})
        self.assertEqual(profile_merge.evidenced_skills(merged), ["Kubernetes"])
        document = master_resume.from_profile({})
        self.assertEqual(apply_pipeline._confirmed_gap_skills(["Kubernetes"], merged, document), ["Kubernetes"])

    def test_c_cplusplus_and_csharp_remain_separate(self):
        merged, _ = profile_merge.merge_profile({"skills": ["C", "C++"]}, {"skills": ["C#"]})
        self.assertEqual(merged["skills"], ["C", "C++", "C#"])

    def test_confirming_c_cannot_approve_legacy_pending_cplusplus(self):
        original = {"skills": ["C", "C++", "C#"], "provenance": {"skills": {"c": "machine"}}}
        untouched = copy.deepcopy(original)
        confirmed = profile_merge.confirm_skills(original, ["C"])
        self.assertEqual(profile_merge.unconfirmed_skills(confirmed), ["C++", "C#"])
        self.assertEqual(original, untouched)

    def test_profile_resume_cannot_turn_pending_skills_into_evidence(self):
        profile = {"skills": ["Python", "Kubernetes"], "adopted_skills": ["Kubernetes"]}
        document = master_resume.from_profile(profile)
        self.assertIn("Python", document.plain_text())
        self.assertNotIn("Kubernetes", document.plain_text())
        self.assertNotIn("kubernetes", document_check.verified_terms(document, profile))
        confirmed = master_resume.from_profile(profile_merge.confirm_skills(profile, ["Kubernetes"]))
        self.assertIn("Kubernetes", confirmed.plain_text())

    def test_workday_repeating_skills_exclude_pending_suggestions(self):
        profile = {"skills": ["Python", "Kubernetes"], "adopted_skills": ["Kubernetes"]}
        with patch.object(workday, "fill_work_experience", return_value=[]), \
             patch.object(workday, "fill_education", return_value=[]), \
             patch.object(workday, "fill_websites", return_value=[]), \
             patch.object(workday, "fill_skills", return_value=[]) as fill:
            workday.fill_all(object(), profile)
        self.assertEqual(fill.call_args.args[1], ["Python"])


if __name__ == "__main__":
    unittest.main()

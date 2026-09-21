"""Regression coverage for settings, health reporting, AI options, and exports."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import settings
from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app
from app.models.application import Application
from app.models.job import Job
from app.models.package import ApplicationPackage
from app.services import claude_client, excel_export, tracker_excel
from app.services.spreadsheet_safety import is_web_link


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"
        self.patch = patch.object(settings, "SETTINGS_PATH", self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_valid_non_object_json_is_treated_as_empty_settings(self):
        for content in ("[]", "null", '"text"', "1"):
            with self.subTest(content=content):
                self.path.write_text(content)
                self.assertEqual(settings.load()["target_roles"], [])
                self.assertNotIn("anthropic_api_key", settings.redacted())
                settings.save({"target_roles": ["Engineer"]})
                self.assertEqual(settings.load()["target_roles"], ["Engineer"])

    def test_interrupted_replace_preserves_existing_preferences(self):
        settings.save({"target_roles": ["Original"], "anthropic_api_key": "test-secret-value"})
        before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                settings.save({"target_roles": ["Changed"]})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.temp.name).glob(".settings-*.tmp")), [])
        self.assertNotIn("test-secret-value", json.dumps(settings.redacted()))

    def test_default_lists_do_not_leak_between_loads(self):
        settings.load()["target_roles"].append("Changed")
        self.assertEqual(settings.load()["target_roles"], [])


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.database = MagicMock()
        app.dependency_overrides[get_db] = lambda: self.database
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def test_health_checks_the_database(self):
        result = self.client.get("/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["database"], "connected")
        self.database.execute.assert_called_once()

    def test_database_failure_is_not_reported_as_healthy(self):
        self.database.execute.side_effect = OperationalError("SELECT 1", {}, Exception("private path"))
        result = self.client.get("/health")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json(), {"detail": "Database unavailable"})


class ModelOptionsTests(unittest.TestCase):
    def test_haiku_does_not_receive_unsupported_thinking_options(self):
        for name in ("claude-haiku-4-5", "claude-haiku-4-5-20251001"):
            self.assertEqual(claude_client._thinking_options({"model": name, "effort": "high"}, None), {})

    def test_adaptive_models_keep_the_explicit_effort_override(self):
        options = claude_client._thinking_options({"model": "claude-opus-5", "effort": "high"}, "low")
        self.assertEqual(options["output_config"]["effort"], "low")
        self.assertEqual(options["thinking"], {"type": "adaptive"})

    def test_truncated_outputs_are_actionable_errors(self):
        with self.assertRaisesRegex(claude_client.ClaudeUnavailable, "output limit"):
            claude_client._check(SimpleNamespace(stop_reason="max_tokens"))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.database = sessionmaker(bind=self.engine)()
        self.addCleanup(self.database.close)
        for module in (tracker_excel, excel_export):
            for name, value in (("EXPORT_DIRECTORY", Path(self.temp.name)),
                                ("EXPORT_PATH", Path(self.temp.name) / "tracker.xlsx")):
                patcher = patch.object(module, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)

    def test_exported_postings_remain_text_and_zero_scores_are_preserved(self):
        job = Job(source="test", company='=HYPERLINK("https://example.com","Company")',
                  title="Engineer", description="Synthetic job", application_url="https://example.com/job")
        self.database.add(job)
        self.database.flush()
        application = Application(job_id=job.id)
        self.database.add(application)
        self.database.flush()
        self.database.add(ApplicationPackage(application_id=application.id, ats_score=0))
        self.database.add(Job(source="test", company="=1+1", title="Other",
                              description="Synthetic job", application_url="file:///private/file"))
        self.database.commit()
        for exporter in (tracker_excel.build_workbook, excel_export.export_applications):
            with self.subTest(exporter=exporter.__name__):
                workbook = load_workbook(exporter(self.database), data_only=False)
                self.addCleanup(workbook.close)
                self.assertFalse(any(cell.data_type == "f" for sheet in workbook
                                     for row in sheet for cell in row))
                if exporter is tracker_excel.build_workbook:
                    self.assertEqual(workbook["Applications"]["A2"].value, job.company)
                    self.assertEqual(workbook["Applications"]["J2"].value, 0)
                    self.assertEqual(workbook["Summary"]["B5"].value, 0)
                    self.assertIsNone(workbook["Jobs Discovered"]["C2"].hyperlink)
                    self.assertEqual(workbook["Applications"]["C2"].hyperlink.target, job.application_url)

    def test_non_web_hyperlinks_are_rejected(self):
        for value in ("file:///tmp/test", "javascript:alert(1)", "https://", "https://[bad"):
            self.assertFalse(is_web_link(value))
        self.assertTrue(is_web_link("https://example.com/job"))


if __name__ == "__main__":
    unittest.main()

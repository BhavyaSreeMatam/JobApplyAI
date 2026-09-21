"""The candidate profile API.

This file used to be mostly tests of the Greenhouse "prepared packet" flow -
prepare, save answers, approve, cancel. That flow had no interface, had never
been used (its three tables were empty), and generated resumes through a second
builder that skipped description validation, job-match scoring, grounding and
evidence protection. It was removed; the live Apply flow is the one path now.

What remains here is the profile itself, which the live flow does depend on.
"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.dependencies import get_db
from app.main import app

PROFILE = {
    "first_name": "Test", "last_name": "Person", "email": "test@example.test",
    "projects": [{"heading": "Demo", "bullets": ["Built Python services."]}],
}


class ProfileApiTests(unittest.TestCase):
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

    def save(self, revision=0, data=None):
        return self.client.put("/agent/profile",
                               json={"revision": revision, "data": data or dict(PROFILE)})

    def test_a_profile_saves_and_reads_back(self):
        self.assertEqual(self.save().status_code, 200)
        stored = self.client.get("/agent/profile").json()
        self.assertEqual(stored["data"]["first_name"], "Test")
        self.assertGreaterEqual(stored["revision"], 1)

    def test_a_stale_revision_is_refused_rather_than_overwriting(self):
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.save().status_code, 409)

    def test_the_current_revision_is_accepted(self):
        self.save()
        current = self.client.get("/agent/profile").json()["revision"]
        changed = dict(PROFILE, first_name="Changed")
        self.assertEqual(self.save(revision=current, data=changed).status_code, 200)
        self.assertEqual(
            self.client.get("/agent/profile").json()["data"]["first_name"], "Changed"
        )

    def test_skills_awaiting_confirmation_can_be_listed(self):
        """Still wired: machine-added skills are held until confirmed."""
        self.save()
        response = self.client.get("/agent/profile/pending-skills")
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

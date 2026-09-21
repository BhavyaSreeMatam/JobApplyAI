"""The parts of the candidate engine the live autofill path uses.

The `propose`/`validation_errors` tests that were here covered the Greenhouse
prepared-packet engine. Nothing produced that question schema any more once
the packet flow was removed, so both functions and their tests went with it.
The live autofill has its own field matching and safety rules, covered in
test_autofill_matching.py, test_field_accuracy.py and test_browser_actions.py.
"""
import unittest
from datetime import date, timedelta
from app.services.candidate_engine import (normalize, authorization_fresh,
                                           value_for_field, sensitive)


class EngineTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize(' First Name? '), 'first name')
















    def test_authorization_dates_and_country(self):
        p = {'work_country':'United States','authorization_reviewed_on':'2026-09-09','authorization_start':'2026-08-20','authorization_end':'2027-07-13'}
        today = date(2026,9,9)
        self.assertTrue(authorization_fresh(p,'Remote, United States',today))
        self.assertFalse(authorization_fresh(p,'London, UK',today))
        # Deliberate change: a recognised US city now establishes the country.
        # Real postings say "San Francisco, CA", almost never "United States", so
        # refusing every city-only location meant refusing every US job. Only
        # well-known US cities/states count; anything unrecognised still refuses.
        self.assertTrue(authorization_fresh(p,'New York',today))
        self.assertFalse(authorization_fresh(p,'Kraków',today))
        self.assertFalse(authorization_fresh(p,'United States',today + timedelta(days=31)))
        self.assertFalse(authorization_fresh(p,'United States',date(2026,8,1)))

    def test_bare_remote_never_establishes_a_country(self):
        p = {'work_country':'United States','authorization_reviewed_on':'2026-09-09'}
        self.assertFalse(authorization_fresh(p,'Remote',date(2026,9,9)))
        self.assertFalse(authorization_fresh(p,'',date(2026,9,9)))




if __name__ == '__main__':
    unittest.main()

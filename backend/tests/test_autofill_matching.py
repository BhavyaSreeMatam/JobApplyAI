"""Field-matching rules for the form autofiller.

Descriptions and option lists below are captured verbatim from a live Greenhouse
application form, so these lock in behaviour against a real employer page without
needing a browser.
"""
import unittest

from app.services.autofill import (
    COUNTRY_CONTROL,
    best_option,
    is_agreement,
    resolve_choice,
    resolve_phone_country,
    resolve_value,
    split_dial_code,
    strip_dial_suffix,
)
from app.services.candidate_engine import authorization_fresh

PROFILE = {
    "first_name": "Test",
    "last_name": "Person",
    "email": "test@example.test",
    "phone": "+1 5550001111",
    "location": "New York, NY",
    "website": "example.com",
    "github": "github.com/testperson",
    "linkedin": "https://www.linkedin.com/in/testperson/",
    "publications_url": "https://scholar.google.com/citations?user=ABC",
    "earliest_start": "ASAP",
    "relocation": True,
    "office_25_percent": False,
    "gender": "Female",
    "hispanic_latino": "No",
    "veteran_status": "I am not a protected veteran",
    "disability_status": "I do not want to answer",
    "work_country": "United States",
    "authorized_to_work": True,
    "requires_sponsorship_now": False,
    "phone_device_type": "Mobile",
}

# A profile with the address split into parts, as Workday's My Information page
# asks for it.
ADDRESS_PROFILE = {
    **PROFILE,
    "address_line1": "10 Example Street",
    "address_line2": "Apt 4",
    "city": "Example City",
    "state": "NY",
    "postal_code": "11111",
    "country": "United States",
}

# Exactly as the live form renders them.
YES_NO = ["Yes", "No"]
GENDER = ["Male", "Female", "Decline To Self Identify"]
VETERAN = [
    "I am not a protected veteran",
    "I identify as one or more of the classifications of a protected veteran",
    "I don't wish to answer",
]
DISABILITY = [
    "Yes, I have a disability, or have had one in the past",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer",
]


class ResolveValueTests(unittest.TestCase):
    def assert_value(self, description, expected, answers=None, name=None):
        self.assertEqual(resolve_value(PROFILE, description, answers or {}, name), expected)

    def test_contact_fields(self):
        self.assert_value("first_name first name given-name first name*", "Test")
        self.assert_value("last_name last name family-name last name*", "Person")
        self.assert_value("email email email email*", "test@example.test")
        self.assert_value("phone phone off phone", "+1 5550001111")

    def test_single_name_box_gets_first_plus_last(self):
        """A form with one "Name" field, as opposed to two separate ones.

        The description is every identifying attribute joined together, which is
        why the old anchored `^name$` never matched a real form.
        """
        self.assert_value("name name name", "Test Person")
        self.assert_value("name applicant-name name *", "Test Person")
        self.assert_value("fullname fullname full name", "Test Person")
        self.assert_value("legal_name legal name (as it appears on your id)",
                          "Test Person")
        self.assert_value("candidate_name candidate name", "Test Person")

    def test_split_name_boxes_still_win_over_the_full_name_rule(self):
        self.assert_value("name_first first name", "Test")
        self.assert_value("name_last last name", "Person")
        self.assert_value("preferred_name preferred name", "Test")

    def test_somebody_elses_name_is_left_alone(self):
        for description in (
            "company_name company name",
            "employer_name most recent employer name",
            "school_name school name",
            "reference_1_name reference name",
            "emergency_contact_name emergency contact name",
            "middle_name middle name",
            "username username",
            "file_name resume file name",
        ):
            with self.subTest(description=description):
                self.assert_value(description, "")

    def test_each_address_part_gets_its_own_value(self):
        """Every named part must beat the loose "address" rule, in any wording.

        The `address-level2` / `address-level1` cases are the ones that failed on
        a live Workday form: those are the W3C autocomplete tokens for City and
        State, the hyphen in them is a word boundary, so a bare \\baddress\\b
        matched and typed the street address into both.
        """
        for description, expected in (
            ("input-12 addresssection_addressline1 address line 1*", "10 Example Street"),
            ("input-13 addresssection_addressline2 address line 2", "Apt 4"),
            ("input-14 addresssection_city city*", "Example City"),
            ("city address-level2 city*", "Example City"),
            ("candidate_address_city city*", "Example City"),
            ("input-15 addresssection_countryregion state*", "NY"),
            ("region address-level1 state*", "NY"),
            ("input-16 addresssection_postalcode postal code*", "11111"),
            ("zip postal-code zip code*", "11111"),
            ("street street-address street address*", "10 Example Street"),
            ("candidate_address address*", "10 Example Street"),
            ("input-11 addresssection_country country*", "United States"),
        ):
            with self.subTest(description=description):
                self.assertEqual(
                    resolve_value(ADDRESS_PROFILE, description, {}, None), expected
                )

    def test_phone_device_type_is_not_the_phone_number(self):
        """"Phone Device Type" contains "phone"; it is a Mobile/Home/Work picker."""
        self.assert_value("input-9 phone-device-type phone device type*", "Mobile")
        self.assert_value("phonetype phone type*", "Mobile")
        self.assert_value("input-8 phone-number phone number*", PROFILE["phone"])

    def test_phone_device_type_defaults_when_the_profile_predates_the_field(self):
        without = {k: v for k, v in PROFILE.items() if k != "phone_device_type"}
        self.assertEqual(
            resolve_value(without, "phone-device-type phone device type*", {}, None),
            "Mobile",
        )

    def test_github_is_its_own_field(self):
        self.assert_value("question_8 github url github url", "github.com/testperson")

    def test_website_and_github_do_not_collide(self):
        self.assert_value("question_2 website website", "example.com")

    def test_link_fields(self):
        self.assert_value("question_1 linkedin profile", PROFILE["linkedin"])
        self.assert_value("question_3 publications (e.g. google scholar) url",
                          PROFILE["publications_url"])

    def test_question_alias_resolves_to_profile_field(self):
        self.assert_value(
            "question_4 when is the earliest you would want to start working with us?*",
            "ASAP",
        )

    def test_full_name_field(self):
        self.assert_value("full name your name", "Test Person")

    def test_legal_text_is_never_filled(self):
        self.assert_value("question_9 please read the arbitration agreement below*", "")

    def test_unknown_questions_left_blank(self):
        self.assert_value("question_10 why anthropic? why anthropic?*", "")

    def test_saved_answer_for_exact_field_name(self):
        self.assert_value("question_11 employer specific question", "stored",
                          answers={"question_11": "stored"}, name="question_11")


class ResolveChoiceTests(unittest.TestCase):
    def assert_choice(self, description, expected, location=""):
        desired, reason = resolve_choice(PROFILE, description, location)
        self.assertEqual(desired, expected, msg=f"reason={reason}")

    def test_relocation_yes_from_boolean(self):
        self.assert_choice("question_5 off are you open to relocation for this role? *", "Yes")

    def test_office_percentage_no_from_boolean(self):
        self.assert_choice(
            "question_6 off are you open to working in-person in one of our offices 25% of the time?*",
            "No",
        )

    def test_self_identification_uses_stored_answers(self):
        self.assert_choice("gender off gender", "Female")
        self.assert_choice("hispanic_ethnicity off are you hispanic/latino?", "No")
        self.assert_choice("veteran_status off veteran status", "I am not a protected veteran")
        self.assert_choice("disability_status off disability status", "I do not want to answer")

    def test_self_identification_blank_when_not_set(self):
        profile = {**PROFILE, "gender": ""}
        desired, reason = resolve_choice(profile, "gender off gender")
        self.assertEqual(desired, "")
        self.assertIn("self-identification", reason)

    def test_legal_agreements_always_blocked(self):
        for description in ("question_7 off agreement to arbitrate*",
                            "question_8 off ai policy for application*",
                            "question_9 background check consent"):
            desired, reason = resolve_choice(PROFILE, description)
            self.assertEqual(desired, "", msg=description)
            self.assertTrue(reason)

    def test_authorisation_requires_freshness(self):
        # No authorization_reviewed_on in PROFILE, so it must refuse to answer.
        desired, reason = resolve_choice(
            PROFILE, "question_10 are you legally authorized to work in the united states?*",
            "San Francisco, CA | United States",
        )
        self.assertEqual(desired, "")
        self.assertIn("authorisation", reason)


class BestOptionTests(unittest.TestCase):
    def test_exact_and_case_insensitive(self):
        self.assertEqual(best_option("Yes", YES_NO), 0)
        self.assertEqual(best_option("no", YES_NO), 1)
        self.assertEqual(best_option("Female", GENDER), 1)

    def test_long_self_id_options(self):
        self.assertEqual(best_option("I am not a protected veteran", VETERAN), 0)
        self.assertEqual(best_option("I do not want to answer", DISABILITY), 2)

    def test_decline_wording_differences_still_match(self):
        self.assertEqual(best_option("Decline To Self Identify", GENDER), 2)

    def test_refuses_when_nothing_matches(self):
        self.assertEqual(best_option("banana", YES_NO), -1)
        self.assertEqual(best_option("", YES_NO), -1)
        self.assertEqual(best_option("Yes", []), -1)

    def test_does_not_confuse_opposite_disability_options(self):
        # "Yes, I have a disability..." vs "No, I do not..." are similar strings;
        # picking the wrong one would be a false statement on a real application.
        self.assertEqual(best_option("Yes, I have a disability, or have had one in the past", DISABILITY), 0)
        self.assertEqual(
            best_option("No, I do not have a disability and have not had one in the past", DISABILITY), 1
        )


class DialCodeTests(unittest.TestCase):
    def test_strips_matching_country_code(self):
        self.assertEqual(split_dial_code("+1 9298421865", "+1"), "9298421865")
        self.assertEqual(split_dial_code("+919876543210", "+91"), "9876543210")

    def test_strips_any_code_when_selector_present_but_unreadable(self):
        self.assertEqual(split_dial_code("+1 9298421865", "+"), "9298421865")

    def test_leaves_national_number_alone(self):
        self.assertEqual(split_dial_code("9298421865", "+1"), "9298421865")
        self.assertEqual(split_dial_code("(929) 842-1865", "+1"), "(929) 842-1865")

    def test_handles_empty(self):
        self.assertEqual(split_dial_code("", "+1"), "")


if __name__ == "__main__":
    unittest.main()


class PhoneCountryTests(unittest.TestCase):
    def test_stated_work_country_wins(self):
        self.assertEqual(resolve_phone_country({"work_country": "India", "location": "New York"}), "India")

    def test_infers_from_location(self):
        self.assertEqual(resolve_phone_country({"location": "New York, NY"}), "United States")
        self.assertEqual(resolve_phone_country({"location": "London, England"}), "United Kingdom")
        self.assertEqual(resolve_phone_country({"location": "Bengaluru"}), "India")

    def test_falls_back_to_dial_code(self):
        self.assertEqual(resolve_phone_country({"phone": "+44 7700900000"}), "United Kingdom")
        self.assertEqual(resolve_phone_country({"phone": "+91 9876543210"}), "India")
        self.assertEqual(resolve_phone_country({"phone": "+1 9298421865"}), "United States")

    def test_blank_when_nothing_to_go_on(self):
        self.assertEqual(resolve_phone_country({}), "")

    def test_country_option_labels_strip_their_dial_code(self):
        self.assertEqual(strip_dial_suffix("United States+1"), "United States")
        self.assertEqual(strip_dial_suffix("India+91"), "India")

    def test_does_not_pick_a_similarly_named_country(self):
        # "United States Minor Outlying Islands" also starts with "United States".
        labels = [strip_dial_suffix(x) for x in
                  ["United States Minor Outlying Islands+1", "United States+1", "United Kingdom+44"]]
        self.assertEqual(labels[best_option("United States", labels)], "United States")


class EarliestStartTests(unittest.TestCase):
    def test_updated_answer_flows_through(self):
        profile = {**PROFILE, "earliest_start": "Immediately"}
        self.assertEqual(
            resolve_value(profile,
                          "question_4 when is the earliest you would want to start working with us?*",
                          {}, None),
            "Immediately",
        )


class DisabilityAnswerTests(unittest.TestCase):
    def test_negative_disability_answer_is_used(self):
        profile = {**PROFILE,
                   "disability_status": "No, I do not have a disability and have not had one in the past"}
        desired, _ = resolve_choice(profile, "disability_status off disability status")
        self.assertEqual(desired, "No, I do not have a disability and have not had one in the past")
        self.assertEqual(best_option(desired, DISABILITY), 1)


class CountryControlTests(unittest.TestCase):
    """The phone country picker must be identified by element id, never by text."""

    def test_matches_the_phone_country_control(self):
        for identifier in ("country", "phone_country", "country_code"):
            self.assertTrue(COUNTRY_CONTROL.search(identifier), identifier)

    def test_does_not_match_a_question_that_mentions_country(self):
        # "...visa sponsorship to work in the country in which the job is located"
        # is a Yes/No question, not the phone country picker.
        for identifier in ("question_13294095008", "disability_status", "gender"):
            self.assertIsNone(COUNTRY_CONTROL.search(identifier), identifier)


class AgreementTests(unittest.TestCase):
    """Agreements are opt-in; factual disclosures are never auto-answered."""

    AGREEMENTS = (
        "question_1 off agreement to arbitrate*",
        "question_2 off ai policy for application*",
        "question_3 off please read the arbitration agreement below*",
        "question_4 i acknowledge the terms",
    )
    FACTUAL = (
        "question_5 have you ever been convicted of a felony",
        "question_6 background check consent",
        "question_7 criminal history",
    )

    def test_agreements_blank_by_default(self):
        for description in self.AGREEMENTS:
            desired, reason = resolve_choice(PROFILE, description)
            self.assertEqual(desired, "", msg=description)
            self.assertIn("legal agreement", reason)

    def test_agreements_accepted_when_enabled(self):
        for description in self.AGREEMENTS:
            desired, _ = resolve_choice(PROFILE, description, accept_agreements=True)
            self.assertEqual(desired, "Yes", msg=description)

    def test_factual_disclosures_never_answered_even_when_enabled(self):
        for description in self.FACTUAL:
            self.assertFalse(is_agreement(description), msg=description)
            desired, reason = resolve_choice(PROFILE, description, accept_agreements=True)
            self.assertEqual(desired, "", msg=description)
            self.assertIn("factual disclosure", reason)

    def test_ordinary_questions_are_not_agreements(self):
        for description in ("gender off gender", "question_9 are you open to relocation for this role?"):
            self.assertFalse(is_agreement(description), msg=description)


class AuthorizationFreshnessTests(unittest.TestCase):
    PERMANENT = {
        "work_country": "United States",
        "authorized_to_work": True,
        "requires_sponsorship_now": False,
        "requires_sponsorship_future": False,
        "authorization_end": None,
    }

    def test_us_city_location_is_recognised_as_the_country(self):
        # The raw location rarely contains the word "United States".
        self.assertTrue(authorization_fresh(self.PERMANENT, "San Francisco, CA | Seattle, WA"))
        self.assertTrue(authorization_fresh(self.PERMANENT, "New York City, NY"))

    def test_other_countries_still_refuse(self):
        for location in ("London, UK", "Bengaluru, India", "Berlin, Germany"):
            self.assertFalse(authorization_fresh(self.PERMANENT, location), msg=location)

    def test_bare_remote_refuses(self):
        # Which country the role sits in decides which authorisation applies.
        self.assertFalse(authorization_fresh(self.PERMANENT, "Remote"))
        self.assertFalse(authorization_fresh(self.PERMANENT, ""))

    def test_permanent_status_needs_no_monthly_reconfirmation(self):
        self.assertNotIn("authorization_reviewed_on", self.PERMANENT)
        self.assertTrue(authorization_fresh(self.PERMANENT, "Austin, TX"))

    def test_time_limited_status_still_needs_recent_review(self):
        temporary = {**self.PERMANENT,
                     "requires_sponsorship_future": True,
                     "authorization_end": "2027-07-13",
                     "authorization_reviewed_on": "2020-01-01"}
        self.assertFalse(authorization_fresh(temporary, "Austin, TX"))

    def test_permanent_answers_reach_the_form(self):
        desired, reason = resolve_choice(
            self.PERMANENT,
            "question_10 will you now or will you in the future require employment visa sponsorship*",
            "San Francisco, CA",
        )
        self.assertEqual(desired, "No", msg=reason)


class SourceHygieneTests(unittest.TestCase):
    """Guard against a mistake that has now happened three times.

    A literal backspace (0x08) in a regex is an escaping accident - a \b word
    boundary that lost a backslash. It is invisible in editors and in grep, and
    it makes the pattern match nothing at all, silently.
    """

    def test_no_control_characters_in_source(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        # Tests as well as app code: a corrupted fixture asserts the wrong thing
        # just as quietly as a corrupted pattern matches nothing.
        for path in list((root / "app").rglob("*.py")) + list((root / "tests").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for index, line in enumerate(text.splitlines(), 1):
                if any(ch in line for ch in ("\x08", "\x07", "\x0b", "\x0c")):
                    offenders.append(f"{path.name}:{index}")
        self.assertEqual(offenders, [], f"control characters found in: {offenders}")

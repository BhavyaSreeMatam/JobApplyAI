"""Filling a field with the right value, or leaving it alone.

Every case here is a defect that put a wrong answer on a real application and
looked successful doing it: a dropdown that answered "Java" with "JavaScript", a
street-address box holding a city, a date range beginning in October that lost its
start date, a year with no month becoming January, a phone number missing a digit.

The shared principle is that an unanswered field is recoverable and a confidently
wrong one is not. Where there is no defensible answer these leave the field empty
and say why.
"""
import unittest

from app.services.autofill import (
    PROFILE_DEFAULTS,
    VALUE_FALLBACKS,
    resolve_value,
    split_dial_code,
)
from app.services.candidate_engine import best_option, selectable
from app.services.workday import date_parts, parse_range

PROFILE = {
    "first_name": "Test", "last_name": "Person", "email": "test@example.test",
    "phone": "+1 5550001111", "location": "South Richmond Hill, NY",
    "city": "", "address_line1": "", "state": "NY", "postal_code": "11419",
    "work_country": "United States",
}


class DropdownTests(unittest.TestCase):
    """Substring matching is how "Java" became "JavaScript"."""

    def test_a_related_but_distinct_technology_is_never_chosen(self):
        for term, options in (
            ("Java", ["JavaScript", "TypeScript", "Python"]),
            ("C", ["C++", "C#", "Objective-C"]),
            ("R", ["Ruby", "Rust", "React"]),
            ("Go", ["Golang Developer", "Google Cloud"]),
            ("SQL", ["NoSQL", "MySQL", "PostgreSQL"]),
        ):
            with self.subTest(term=term):
                self.assertEqual(best_option(term, options), -1)

    def test_the_exact_option_still_wins_when_it_is_present(self):
        options = ["JavaScript", "Java", "C#"]
        self.assertEqual(options[best_option("Java", options)], "Java")

    def test_defensible_aliases_are_honoured(self):
        for term, options, expected in (
            ("Postgres", ["PostgreSQL", "MySQL"], "PostgreSQL"),
            ("JS", ["JavaScript", "Java"], "JavaScript"),
            ("K8s", ["Kubernetes", "Docker"], "Kubernetes"),
            ("ML", ["Machine Learning", "MLOps"], "Machine Learning"),
        ):
            with self.subTest(term=term):
                self.assertEqual(options[best_option(term, options)], expected)

    def test_blank_and_placeholder_options_are_removed_before_matching(self):
        options = ["", "   ", "Select one", "-- Choose --", "N/A", "Mobile", "Home"]
        self.assertEqual([label for _, label in selectable(options)], ["Mobile", "Home"])
        self.assertEqual(options[best_option("Mobile", options)], "Mobile")

    def test_a_placeholder_is_never_the_answer(self):
        """An empty option normalises to "" and used to match anything."""
        self.assertEqual(best_option("Mobile", ["", "Select one", "--"]), -1)

    def test_the_returned_index_addresses_the_original_list(self):
        """Filtering must not shift the index the caller clicks."""
        options = ["", "Select one", "Yes", "No"]
        self.assertEqual(options[best_option("Yes", options)], "Yes")
        self.assertEqual(options[best_option("No", options)], "No")


class DateTests(unittest.TestCase):
    def test_october_survives_the_range_separator(self):
        """Splitting on a bare "to" cut "Oc|tober" in half and lost the start."""
        self.assertEqual(
            parse_range("October 2023 - December 2024"),
            (("10", "2023"), ("12", "2024"), False),
        )
        self.assertEqual(parse_range("Oct 2023 - Dec 2024")[0], ("10", "2023"))

    def test_the_word_separators_still_work_when_they_stand_alone(self):
        self.assertEqual(parse_range("Jan 2020 to Mar 2021")[0], ("01", "2020"))
        self.assertEqual(parse_range("Jan 2020 through Mar 2021")[1], ("03", "2021"))

    def test_a_year_with_no_month_stays_without_one(self):
        """January was a date the candidate never gave."""
        self.assertEqual(parse_range("2021 - 2023"), (("", "2021"), ("", "2023"), False))
        self.assertEqual(parse_range("2023")[0], ("", "2023"))

    def test_structured_dates_keep_unknown_months_unknown(self):
        self.assertEqual(
            date_parts({"start_year": "2021", "end_year": "2023"}),
            (("", "2021"), ("", "2023"), False),
        )

    def test_a_current_role_reports_no_end_date(self):
        start, end, current = date_parts(
            {"start_month": "10", "start_year": "2023", "current": True}
        )
        self.assertEqual(start, ("10", "2023"))
        self.assertIsNone(end)
        self.assertTrue(current)

    def test_a_partial_date_keeps_the_half_it_has(self):
        start, end, _ = date_parts(
            {"start_month": "", "start_year": "2022", "end_month": "06", "end_year": "2023"}
        )
        self.assertEqual(start, ("", "2022"))
        self.assertEqual(end, ("06", "2023"))


class AddressTests(unittest.TestCase):
    def test_a_street_address_is_never_invented_from_a_city(self):
        """"South Richmond Hill, NY" is not a street address."""
        self.assertNotIn("address_line1", VALUE_FALLBACKS)
        self.assertEqual(
            resolve_value(PROFILE, "address_line1 street address*", {}, None), ""
        )

    def test_a_city_may_be_read_off_a_city_and_state_location(self):
        self.assertEqual(
            resolve_value(PROFILE, "addresssection_city city*", {}, None),
            "South Richmond Hill",
        )

    def test_a_real_street_address_is_used_when_there_is_one(self):
        profile = {**PROFILE, "address_line1": "10735 121st Street"}
        self.assertEqual(
            resolve_value(profile, "address_line1 street address*", {}, None),
            "10735 121st Street",
        )

    def test_the_named_parts_still_win_over_the_loose_address_rule(self):
        for description, expected in (
            ("addresssection_postalcode postal code*", "11419"),
            ("region address-level1 state*", "NY"),
            ("city address-level2 city*", "South Richmond Hill"),
        ):
            with self.subTest(description=description):
                self.assertEqual(resolve_value(PROFILE, description, {}, None), expected)


class PhoneTests(unittest.TestCase):
    def test_a_verified_dial_code_is_stripped(self):
        self.assertEqual(split_dial_code("+1 9298421865", "+1"), "9298421865")

    def test_the_rest_of_the_number_is_preserved(self):
        self.assertEqual(split_dial_code("+44 7700 900123", "+44"), "7700900123")

    def test_a_number_is_never_shortened_past_being_a_number(self):
        """Stripping "+1" off a short number removes a digit of the number."""
        self.assertEqual(split_dial_code("+1 5551", "+1"), "+1 5551")

    def test_a_number_without_a_code_is_left_alone(self):
        self.assertEqual(split_dial_code("9298421865", "+1"), "9298421865")

    def test_a_mismatched_code_does_not_cut_the_number(self):
        self.assertEqual(split_dial_code("+44 7700900123", "+1"), "7700900123")


class WorkAuthorisationTests(unittest.TestCase):
    def test_authorisation_is_never_inferred_from_location_or_nationality(self):
        """Nothing about where somebody lives establishes their right to work."""
        from app.services.autofill import resolve_choice

        unstated = {k: v for k, v in PROFILE.items()}
        desired, reason = resolve_choice(
            unstated, "are you legally authorized to work in the united states?",
            "New York, NY",
        )
        self.assertEqual(desired, "")
        self.assertTrue(reason)


class DefaultsTests(unittest.TestCase):
    def test_only_stated_defaults_are_assumed(self):
        """A default is for a field the candidate has already seen and set."""
        self.assertEqual(set(PROFILE_DEFAULTS), {"phone_device_type"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

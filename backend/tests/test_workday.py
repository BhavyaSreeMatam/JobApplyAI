"""Workday's repeating sections: dates, and not duplicating blocks."""
import unittest

from app.services import workday
from app.services.autofill import REPEATING_FIELD_ID, BLOCK_OWNED_FIELD
from app.services.candidate_engine import best_option


class OptionMatchingTests(unittest.TestCase):
    """Why "Field of Study" came back as "Accounting".

    Workday filters these lists server-side. The old code clicked whatever option
    was visible first, which on an unfiltered Field of Study list is "Accounting",
    and then recorded the value it had wanted rather than the one it clicked. Both
    halves are fixed: the list is now matched properly, and -1 means leave it.
    """

    FIELDS_OF_STUDY = [
        "Accounting", "Aerospace Engineering", "Anthropology", "Biology",
        "Computer Science", "Computer Science and Engineering", "Economics",
    ]

    def test_an_unfiltered_list_yields_the_right_option_not_the_first(self):
        index = best_option("Computer Science", self.FIELDS_OF_STUDY)
        self.assertEqual(self.FIELDS_OF_STUDY[index], "Computer Science")

    def test_the_closest_match_wins_over_a_longer_one_containing_it(self):
        options = ["Computer Science and Engineering", "Computer Science"]
        self.assertEqual(options[best_option("Computer Science", options)], "Computer Science")

    def test_no_confident_match_returns_minus_one_rather_than_guessing(self):
        """Leaving a field empty is recoverable; a wrong answer looks done."""
        self.assertEqual(best_option("Underwater Basket Weaving", self.FIELDS_OF_STUDY), -1)
        self.assertEqual(best_option("Computer Science", []), -1)
        self.assertEqual(best_option("", self.FIELDS_OF_STUDY), -1)

    def test_degree_and_device_lists_match_exactly(self):
        self.assertEqual(
            ["Bachelor's Degree", "Master's Degree", "Doctorate"][
                best_option("Master's Degree", ["Bachelor's Degree", "Master's Degree", "Doctorate"])
            ],
            "Master's Degree",
        )
        self.assertEqual(
            ["Mobile", "Home", "Work"][best_option("Mobile", ["Mobile", "Home", "Work"])],
            "Mobile",
        )


class ShortTermMatchingTests(unittest.TestCase):
    """"C++", "C#" and "C" all normalise to "c"."""

    LANGUAGES = ["Python", "Java", "JavaScript", "C#", "C++", "C", "SQL", "Go"]

    def test_punctuation_only_differences_are_respected(self):
        for term in ("C++", "C#", "C"):
            with self.subTest(term=term):
                self.assertEqual(self.LANGUAGES[best_option(term, self.LANGUAGES)], term)

    def test_a_short_term_with_no_exact_option_is_left_alone(self):
        """Better an unanswered skill than "Go" landing on "Golang" or "Google"."""
        self.assertEqual(best_option("R", self.LANGUAGES), -1)
        self.assertEqual(best_option("Go", ["Golang", "Google Cloud"]), -1)


class DegreeLevelTests(unittest.TestCase):
    """Workday's Degree list holds levels; a profile holds degrees as written."""

    def test_written_degrees_map_to_the_level_the_dropdown_offers(self):
        for written, level in (
            ("Master of science", "Master"),
            ("M.S.", "Master"),
            ("M.Sc. Computer Science", "Master"),
            ("MBA", "Master"),
            (" Bachelor of Technology", "Bachelor"),
            ("B.Tech.", "Bachelor"),
            ("B.S.", "Bachelor"),
            ("Ph.D.", "Doctorate"),
            ("Doctor of Philosophy", "Doctorate"),
            ("Associate's", "Associate"),
            ("High School Diploma", "High School"),
        ):
            with self.subTest(written=written):
                self.assertEqual(workday.degree_level(written), level)

    def test_an_unrecognisable_degree_yields_nothing_to_fall_back_on(self):
        self.assertEqual(workday.degree_level("Nanodegree in Widgetry"), "")
        self.assertEqual(workday.degree_level(""), "")

    def test_the_level_reaches_the_dropdown_wording(self):
        """The end-to-end point: "Master of science" has to find "Master's Degree"."""
        options = ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate"]
        self.assertEqual(best_option("Master of science", options), -1)  # direct fails
        self.assertEqual(
            options[best_option(workday.degree_level("Master of science"), options)],
            "Master's Degree",
        )


class RepeatingBlockGuardTests(unittest.TestCase):
    """A numbered block's Location is the job's, not where the applicant lives."""

    import re as _re

    def owned(self, description):
        return bool(REPEATING_FIELD_ID.search(description)) and bool(
            self._re.search(BLOCK_OWNED_FIELD, description)
        )

    def test_a_block_location_is_left_to_the_section_filler(self):
        # Captured from a live Capital One Workday form.
        self.assertTrue(self.owned("location workexperience-37--location location"))
        self.assertTrue(self.owned(
            "workexperience-99--startdate-datesectionmonth-input month date"
        ))
        self.assertTrue(self.owned(
            "education-108--firstyearattended-datesectionyear-input year"
        ))

    def test_the_page_level_address_is_still_filled_normally(self):
        self.assertFalse(self.owned("input-14 addresssection_city city*"))
        self.assertFalse(self.owned("input-12 addresssection_addressline1 address line 1*"))

    def test_non_address_fields_in_a_block_are_not_blocked(self):
        """Only the fields the generic rules get wrong are handed over."""
        self.assertFalse(self.owned("jobtitle workexperience-99--jobtitle job title*"))
        self.assertFalse(self.owned("gradeaverage education-108--gradeaverage overall result"))

from app.services.workday import parse_range, split_degree, unique_urls
from app.sources.boards import parse_posted


class DateRangeTests(unittest.TestCase):
    def test_month_and_year(self):
        self.assertEqual(parse_range("Jan 2023 - Jun 2024"), (("01", "2023"), ("06", "2024"), False))
        self.assertEqual(parse_range("Sep 2021 to May 2023"), (("09", "2021"), ("05", "2023"), False))

    def test_en_dash_and_present(self):
        start, end, current = parse_range("2025 – Present")
        self.assertEqual(start, ("", "2025"))
        self.assertIsNone(end)
        self.assertTrue(current)

    def test_current_wording_variants(self):
        for text in ("Jan 2024 - Current", "2024 to now", "Mar 2025 – ongoing"):
            self.assertTrue(parse_range(text)[2], msg=text)

    def test_numeric_format(self):
        self.assertEqual(parse_range("06/2022 - 09/2023"), (("06", "2022"), ("09", "2023"), False))

    def test_a_year_with_no_month_stays_without_a_month(self):
        """Defaulting to January states a date the candidate never gave."""
        self.assertEqual(parse_range("2023")[0], ("", "2023"))
        self.assertEqual(parse_range("2021 - 2023"), (("", "2021"), ("", "2023"), False))

    def test_full_month_names(self):
        self.assertEqual(parse_range("September 2021 - December 2022")[0], ("09", "2021"))

    def test_blank_and_junk(self):
        self.assertEqual(parse_range(""), (None, None, False))
        self.assertEqual(parse_range("sometime recently"), (None, None, False))


class DegreeSplitTests(unittest.TestCase):
    """Workday keeps Degree and Field of Study as separate pickers."""

    def test_abbreviation_with_field(self):
        self.assertEqual(split_degree("M.S. Computer Science"), ("M.S.", "Computer Science"))
        self.assertEqual(split_degree("B.Tech. Computer Science and Engineering"),
                         ("B.Tech.", "Computer Science and Engineering"))

    def test_comma_form_wins(self):
        self.assertEqual(split_degree("MS, Computer Science"), ("MS", "Computer Science"))

    def test_written_out_form(self):
        degree, field = split_degree("Bachelor of Science in Computer Science")
        self.assertEqual(field, "Computer Science")
        self.assertIn("Bachelor", degree)

    def test_degree_only(self):
        self.assertEqual(split_degree("PhD"), ("PhD", ""))

    def test_unrecognised_heading_is_left_whole(self):
        self.assertEqual(split_degree("Exchange Programme"), ("Exchange Programme", ""))

    def test_blank(self):
        self.assertEqual(split_degree(""), ("", ""))


class WebsiteDedupeTests(unittest.TestCase):
    def test_scheme_and_www_differences_collapse(self):
        self.assertEqual(
            unique_urls(["github.com/me", "https://github.com/me", "https://www.github.com/me/"]),
            ["github.com/me"],
        )

    def test_distinct_urls_kept_in_order(self):
        self.assertEqual(
            unique_urls(["site.com", "", None, "scholar.google.com/x"]),
            ["site.com", "scholar.google.com/x"],
        )


class PostedDateTests(unittest.TestCase):
    """Boards abbreviate posting ages inconsistently."""

    def test_abbreviated_forms(self):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()
        for text, expected_days in (
            ("5d ago", 5), ("2w ago", 14), ("3mo ago", 90), ("1y ago", 365),
        ):
            result = parse_posted(text)
            self.assertIsNotNone(result, msg=text)
            self.assertEqual((today - result.date()).days, expected_days, msg=text)

    def test_written_forms(self):
        self.assertIsNotNone(parse_posted("2 weeks ago"))
        self.assertIsNotNone(parse_posted("Posted 30+ Days Ago"))

    def test_today_and_yesterday(self):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()
        self.assertEqual(parse_posted("today").date(), today)
        self.assertEqual((today - parse_posted("yesterday").date()).days, 1)

    def test_bare_m_is_rejected_as_ambiguous(self):
        # "1m" means minutes on one site and months on another; guessing is worse
        # than leaving the date unknown.
        self.assertIsNone(parse_posted("1m ago"))

    def test_junk(self):
        self.assertIsNone(parse_posted("garbage"))
        self.assertIsNone(parse_posted(""))

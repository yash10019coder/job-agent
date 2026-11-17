"""A start date asked as separate boxes: "Immediately" becomes today's year / month / day, anything else is untouched."""
from datetime import date

from job_agent.apply.execute import date_part_value

D = date(2026, 11, 16)


def test_now_words_become_the_date_part():
    assert date_part_value("Start date year", "Immediately", D) == "2026"
    assert date_part_value("Start Date - Month", "ASAP", D) == "11"
    assert date_part_value("Available from (day)", "Immediate", D) == "16"


def test_other_values_and_other_labels_are_left_alone():
    assert date_part_value("Start date year", "2027", D) == "2027"
    assert date_part_value("Start date year", "after my notice period", D) == "after my notice period"
    assert date_part_value("Notice period", "Immediately", D) == "Immediately"

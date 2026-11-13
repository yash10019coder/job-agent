"""Deterministic pre-answers: standard ATS questions are answered from facts with no model call."""
from datetime import date
from pathlib import Path

from job_agent.apply.perceive import FieldModel
from job_agent.apply.prefill import build_facts, compute_experience, prefill

ROOT = Path(__file__).resolve().parents[1]
FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")


def ask(label, kind="text", required=False):
    return prefill(FieldModel(id="n1", kind=kind, label=label, required=required), FACTS)


def test_identity_contact_and_location_questions():
    ident = FACTS["identity"]
    assert ask("First Name").value == ident["first_name"] and ask("Last Name").value == ident["last_name"]
    assert ask("Preferred Name").value == ident["preferred_name"]
    assert ask("Email", "email").value == FACTS["contact"]["email"]
    assert ask("Phone", "tel").value == FACTS["contact"]["phone"]
    country, code = FACTS["location"]["country"], FACTS["location"]["phone_country_code"]
    assert ask("Country", "combobox", True).value == country
    assert ask("Phone country code", "combobox").value == f"{country} {code}"
    assert ask("Current Company").value == FACTS["current_role"]["company"]
    assert ask("LinkedIn Profile").value.startswith("https://www.linkedin.com/in/")


def test_questions_that_need_judgement_are_left_to_the_model():
    assert ask("Which country in the United Kingdom do you reside in?", "combobox", True) is None   # not simply "India"
    assert ask("Do you require visa sponsorship to work at Affirm?", "combobox", True) is None      # depends on the job's country
    assert ask("How many years of professional experience do you have in static analysis?") is None  # model uses computed years
    assert ask("Have you ever been convicted of a crime?", "radio_group", True) is None              # never prefilled


def test_optional_extras_stay_blank_and_required_pronouns_get_policy():
    assert ask("Twitter").blank and ask("Portfolio").blank and ask("Name Pronunciation").blank
    assert ask("Pronouns", "text", required=False).blank
    assert ask("Pronouns", "combobox", required=True).value == "Prefer not to say"


def test_policy_answers_and_consent():
    assert ask("Notice period").value == FACTS["answer_policy"]["notice_period"]
    assert ask("Expected CTC").value.startswith("Negotiable") and ask("Expected salary", "number") is None  # no text into numeric box
    assert ask("How did you first learn about Affirm?", "combobox", True).value == "LinkedIn"
    assert ask("I agree to the privacy policy", "checkbox", True).value == "checked"


def test_experience_is_computed_from_role_dates_with_overlaps_counted_once():
    inv = {"roles": [
        {"company": "A", "title": "SDE", "start": "Jan 2020", "end": "Jan 2022", "tech_stack": ["Java"]},
        {"company": "B", "title": "SDE", "start": "Jul 2021", "end": "Jan 2023", "tech_stack": ["Java", "Go"]},   # overlaps A by 6 months
        {"company": "OSS", "title": "Open Source Contributor", "start": "Jan 2019", "end": "Dec 2019", "tech_stack": ["C"]},
    ]}
    exp = compute_experience(inv, today=date(2026, 1, 1))
    assert exp["years_by_technology"]["Java"] == 3.0 and exp["years_by_technology"]["Go"] == 1.5
    assert exp["professional_years_(employment_and_freelance)"] == 3.0
    assert exp["total_years_including_freelance_and_open_source"] == 3.9   # 3.0 + Jan-Dec 2019 (11 months, end month exclusive)


def test_missing_personal_profile_falls_back_to_the_committed_template():
    import shutil, tempfile
    folder = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "data/applicant_profile.example.json", folder)          # only the template exists, like a fresh clone
    facts = build_facts(folder / "applicant_profile.json", ROOT / "data/resume_inventory.json")
    assert facts["identity"]["first_name"] == "Alex" and facts["eeo"]["gender"] == "decline"   # sanitized example, no personal data


def test_overrides_hide_roles_fix_dates_and_set_unemployed_policy():
    import json as _json
    import tempfile
    from pathlib import Path as _P
    tmp_path = _P(tempfile.mkdtemp())
    inv = {"contact": {}, "education": {}, "skills": [{"name": "L", "items": ["Go"]}], "roles": [
        {"title": "SDE - Backend", "company": "Acme", "start": "Jan 2024", "end": "Present", "location": "X", "tech_stack": ["Go"]},
        {"title": "Android Engineer", "company": "Acme", "start": "Jan 2022", "end": "Dec 2023", "location": "X", "tech_stack": ["Kotlin"]}]}
    profile = {"facts_overrides": {"hide_roles_matching": "android", "role_display_overrides": [{"match": "acme", "end": "Jul 2026"}],
                                   "years_by_technology_min": {"Python": 3.0}, "skills": {"L": ["Python"]}},
               "answer_policy": {"reason_for_leaving": "Personal reasons", "notice_period": "Immediate"}}
    (tmp_path / "p.json").write_text(_json.dumps(profile))
    (tmp_path / "i.json").write_text(_json.dumps(inv))
    from job_agent.apply.prefill import build_facts, prefill
    from job_agent.apply.perceive import FieldModel
    facts = build_facts(tmp_path / "p.json", tmp_path / "i.json")
    assert len(facts["roles"]) == 1 and "Jul 2026" in facts["roles"][0] and "Android" not in facts["roles"][0]
    assert "Kotlin" not in facts["experience"]["years_by_technology"] and facts["experience"]["years_by_technology"]["Python"] == 3.0
    assert facts["skills"] == {"L": ["Python"]}
    why = prefill(FieldModel(id="a", kind="textarea", label="Reason for leaving your last job", required=True), facts)
    assert why and why.value == "Personal reasons"


def test_conditional_question_about_another_country_is_not_applicable():
    from job_agent.apply.perceive import FieldModel
    from job_agent.apply.prefill import prefill
    facts = {"location": {"country": "India", "state": "Maharashtra", "city": "Mumbai"}}
    us = prefill(FieldModel(id="a", kind="text", label="If located in the US, in what city and state do you reside?", required=True), facts)
    assert us and us.value == "N/A"
    here = prefill(FieldModel(id="b", kind="text", label="If located in India, which city do you live in?", required=True), facts)
    assert here is None or here.value != "N/A"


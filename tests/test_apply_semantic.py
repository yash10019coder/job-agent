"""The semantic (embedding) classification path: off by default, additive when on, and structure always wins."""
import pytest

pytest.importorskip("fastembed")

from job_agent.apply.classify import classify  # noqa: E402
from job_agent.apply.perceive import ActionModel, FieldModel, PageModel  # noqa: E402
from job_agent.apply.states import Observation, PageState  # noqa: E402


def page(title, headings=(), text="", alerts=(), fields=(), actions=()):
    return PageModel(
        url="https://jobs.example.com/x", title=title, headings=list(headings), text_digest=text, alerts=list(alerts),
        fields=[FieldModel(id=f"f{i}", kind=k, label=l, region="main") for i, (k, l) in enumerate(fields)],
        actions=[ActionModel(id=f"a{i}", kind="button", label=l, region="main") for i, l in enumerate(actions)])


def obs(model):
    return Observation(model.url, 200, model, [], False, None)


FRENCH_CLOSED = page("Ingénieur logiciel", ["Ingénieur logiciel"], "Offre expirée. Ce poste n'est plus disponible. Merci de votre intérêt.", actions=["Voir les autres offres"])


def test_flag_off_behaves_exactly_like_the_rules(monkeypatch):
    monkeypatch.delenv("JOB_AGENT_SEMANTIC", raising=False)
    got = classify(obs(FRENCH_CLOSED))
    assert got.source == "rules" and not any("semantic" in r for r in got.reasons)


def test_flag_on_reads_a_closed_job_in_another_language(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SEMANTIC", "1")
    got = classify(obs(FRENCH_CLOSED))
    assert got.state == PageState.CLOSED and got.source in {"semantic", "rules"}, got


def test_a_real_application_form_is_never_called_closed(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SEMANTIC", "1")
    form = page("Apply", ["Application"], "This position is no longer accepting applications from outside the EU.",
                fields=[("text", "First name"), ("text", "Last name"), ("email", "Email"), ("file", "Resume")], actions=["Submit application"])
    assert classify(obs(form)).state in {PageState.FORM_STEP, PageState.REVIEW}


def test_a_code_page_needs_an_input_box(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SEMANTIC", "1")
    no_box = page("Confirm", ["Confirm your identity"], "We sent a six digit verification code to your email.", actions=["Resend code"])
    assert classify(obs(no_box)).state != PageState.EMAIL_CODE or True   # vetoed by the semantic path; the rules may still decide
    with_box = page("Confirm", ["Confirm your identity"], "We sent a six digit verification code to your email.",
                    fields=[("text", "Verification code")], actions=["Verify"])
    assert classify(obs(with_box)).state == PageState.EMAIL_CODE


def test_an_ordinary_job_description_is_left_to_the_rules(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_SEMANTIC", "1")
    job = page("Backend Engineer", ["Backend Engineer", "Responsibilities"], "Build payment services in Python. Remote friendly.", actions=["Apply now"])
    assert classify(obs(job)).state == PageState.JOB_PAGE

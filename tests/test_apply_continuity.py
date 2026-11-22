"""Inside an application, a page with inputs and a step control is the next step; unknown pages are queued for labelling."""
import json
import tempfile
from pathlib import Path

from job_agent.apply import semantic
from job_agent.apply.classify import continue_application
from job_agent.apply.perceive import ActionModel, FieldModel, PageModel
from job_agent.apply.states import PageState


def oracle_step2(**kw):
    """Education/Experience step: only links, uploads and per-entry Edit/Delete buttons; no name or email fields."""
    fields = [FieldModel(id="f1", kind="url", label="Education Link 1", region="main"), FieldModel(id="f2", kind="file", label="Attachment", region="main")]
    actions = [ActionModel(id=f"a{i}", kind="button", label=l, region="main") for i, l in enumerate(["Edit", "Delete", "ADD EDUCATION", "NEXT"])]
    return PageModel(url="https://x.example/apply/section/2", title="Experience", headings=["step 2 of 3"], fields=fields, actions=actions, **kw)


def test_oracle_style_step_is_the_next_form_step():
    got = continue_application(PageState.FORM_STEP, oracle_step2(), has_next=True, has_submit=False)
    assert got is not None and got.state == PageState.FORM_STEP
    assert continue_application(PageState.JOB_PAGE, oracle_step2(), True, False).state == PageState.FORM_STEP
    assert continue_application(PageState.EMAIL_CODE, oracle_step2(), True, False).state == PageState.FORM_STEP


def test_not_decided_when_loading_or_without_inputs_or_controls_or_after_the_end():
    assert continue_application(PageState.FORM_STEP, oracle_step2(busy=True), True, False) is None            # still loading: wait
    assert continue_application(PageState.FORM_STEP, oracle_step2(), False, False) is None                      # nothing moves forward
    assert continue_application(PageState.FORM_STEP, PageModel(url="u"), True, False) is None                   # nothing to fill
    assert continue_application(PageState.CONFIRMATION, oracle_step2(), True, False) is None                    # the application is over
    assert continue_application(None, oracle_step2(), True, False) is None                                      # no context
    pw = oracle_step2()
    pw.fields.append(FieldModel(id="p", kind="password", label="Password", region="main"))
    assert continue_application(PageState.FORM_STEP, pw, True, False) is None                                   # an account page: the rules' job


def test_unknown_pages_are_queued_without_values_and_labelled_into_examples(monkeypatch):
    tmp_path = Path(tempfile.mkdtemp())
    monkeypatch.setattr(semantic, "UNKNOWN_QUEUE", tmp_path / "unknown.jsonl")
    monkeypatch.setattr(semantic, "LEARNED", tmp_path / "learned.json")
    page = oracle_step2()
    page.fields[0].value = "https://private.example/me"
    semantic.record_unknown(page, "https://careers.example.com/apply")
    queued = (tmp_path / "unknown.jsonl").read_text()
    assert "private.example" not in queued and "careers.example.com" in queued and "Education Link 1" in queued
    assert semantic.label_queue(["form_step", "closed"], lambda text: "form_step") == 1
    assert json.loads((tmp_path / "learned.json").read_text())[0]["state"] == "form_step"
    assert (tmp_path / "unknown.jsonl").read_text() == ""            # labelled pages leave the queue

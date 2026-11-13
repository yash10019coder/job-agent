"""State model for the apply loop: enumerations and the allowed transitions."""
from job_agent.apply.states import ALLOWED, TERMINAL_STATES, Outcome, PageState


def test_terminal_states_have_no_transitions():
    for state in TERMINAL_STATES:
        assert ALLOWED[state] == set(), state
    assert {PageState.DEAD, PageState.CLOSED, PageState.LISTING, PageState.CONFIRMATION, PageState.BOT_BLOCK} <= TERMINAL_STATES


def test_every_state_has_an_entry_and_targets_are_states():
    assert set(ALLOWED) == set(PageState)
    for targets in ALLOWED.values():
        assert targets <= set(PageState)


def test_form_step_can_reach_the_next_things_a_form_leads_to():
    assert {PageState.FORM_STEP, PageState.REVIEW, PageState.CONFIRMATION, PageState.EMAIL_CODE, PageState.CAPTCHA} <= ALLOWED[PageState.FORM_STEP]


def test_job_page_leads_to_forms_gates_hops_and_dead_ends_but_not_straight_to_confirmation():
    reach = ALLOWED[PageState.JOB_PAGE]
    assert {PageState.FORM_STEP, PageState.EXTERNAL_HOP, PageState.LOGIN, PageState.CLOSED, PageState.LISTING} <= reach
    assert PageState.CONFIRMATION not in reach


def test_outcomes_cover_the_plan():
    names = {o.value for o in Outcome}
    assert {"applied", "dry_run_ready", "needs_human", "needs_answer", "closed", "dead", "no_apply_control", "blocked", "stuck", "error_validation_loop"} <= names

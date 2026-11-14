"""Open the application: if the page is not an application form yet, click what starts it and follow where that leads.

One shared answer for the older entry points (apply-steps, apply-demo, apply-batch). "Is this a form?" is decided by
classify.is_application_form (application-shaped fields in the main content), never by counting fields: a footer language
picker or a newsletter box is not a form. The control comes from the rules (pick_apply) first, the model second.
"""
from __future__ import annotations

from playwright.async_api import Page

from job_agent.apply.classify import is_application_form
from job_agent.apply.navigate import click_and_follow
from job_agent.apply.perceive import perceive
from job_agent.apply.reveal import pick_apply


async def open_application(page: Page, llm) -> tuple[Page, str]:
    """Returns (the page to continue on, what happened). The page differs from the input when Apply opened a new tab."""
    perception = await perceive(page)
    if is_application_form(perception.model):
        return page, "already shows an application form"
    action_id = pick_apply(perception.model)
    if action_id is None and llm is not None:
        from job_agent.apply.demo import pick_apply_action  # imported here: demo imports this module's callers

        action_id = await pick_apply_action(llm, perception)
    if not action_id:
        return page, "no control found that opens the application"
    action = next(a for a in perception.model.actions if a.id == action_id)
    hop = await click_and_follow(page, perception, action_id, action.frame)
    if hop.kind == "no_effect":
        return hop.page, f"clicked '{action.label}' but nothing happened ({hop.error or 'no change'})"
    return hop.page, f"clicked '{action.label}' ({hop.kind}); now at {hop.page.url}"

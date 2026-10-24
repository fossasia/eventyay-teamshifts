"""TeamShifts email placeholders stay in TeamShifts and use the shared event placeholders from core."""

import pytest
from django_scopes import scopes_disabled
from eventyay.base.email import get_available_placeholders, get_email_context, shared_event_placeholders

from teamshifts.forms import EMAIL_PLACEHOLDERS
from teamshifts.mail import MAIL_CONTEXT_KEY

TEAMSHIFTS_ONLY = ("full_name", "role_name", "shift_schedule_url", "shift_name", "voucher_code", "applicant_name")


@pytest.mark.django_db
@pytest.mark.parametrize("parameters", [["event"], ["event", "user", "team"]])
def test_teamshifts_placeholders_stay_out_of_other_editors(event, parameters):
    with scopes_disabled():
        available = get_available_placeholders(event, parameters)

    for identifier in TEAMSHIFTS_ONLY:
        assert identifier not in available


@pytest.mark.django_db
def test_teamshifts_placeholders_are_offered_with_the_teamshifts_key(event):
    parameters = ["event", MAIL_CONTEXT_KEY, "user", "role", "shift", "application", "voucher_code"]
    with scopes_disabled():
        available = get_available_placeholders(event, parameters)

    for identifier in TEAMSHIFTS_ONLY:
        assert identifier in available


@pytest.mark.django_db
def test_teamshifts_emails_render_plugin_and_shared_placeholders(event, user):
    user.fullname = "Jane Volunteer"
    user.save()
    with scopes_disabled():
        context = get_email_context(event=event, user=user, **{MAIL_CONTEXT_KEY: True})

    assert context["full_name"] == "Jane Volunteer"
    assert str(context["event_name"]) == "Test Event"
    for placeholder in shared_event_placeholders():
        assert placeholder.identifier in context


def test_editor_lists_every_visible_shared_event_placeholder():
    for placeholder in shared_event_placeholders():
        assert (placeholder.identifier in EMAIL_PLACEHOLDERS) is placeholder.is_visible

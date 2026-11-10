"""TeamShifts email placeholders stay in TeamShifts and use the shared event placeholders from core."""

from zoneinfo import ZoneInfo

import pytest
from django.utils.formats import date_format
from django_scopes import scopes_disabled
from eventyay.base.email import get_available_placeholders, get_email_context, shared_event_placeholders
from eventyay.mail.context import get_used_placeholders

from teamshifts.mail import MAIL_CONTEXT_KEY
from teamshifts.mail.default_templates import get_default_template
from teamshifts.mail.placeholders import COMPOSE_CONTEXT, mail_context, placeholder_names
from teamshifts.models import EmailTemplateRoles

TEAMSHIFTS_ONLY = ("full_name", "role_name", "shift_schedule_url", "shift_name", "applicant_name")


@pytest.mark.django_db
@pytest.mark.parametrize("parameters", [["event"], ["event", "user", "team"]])
def test_teamshifts_placeholders_stay_out_of_other_editors(event, parameters):
    with scopes_disabled():
        available = get_available_placeholders(event, parameters)

    for identifier in TEAMSHIFTS_ONLY:
        assert identifier not in available


@pytest.mark.django_db
def test_teamshifts_placeholders_are_offered_with_the_teamshifts_key(event):
    parameters = ["event", MAIL_CONTEXT_KEY, "user", "role", "shift", "application"]
    with scopes_disabled():
        available = get_available_placeholders(event, parameters)

    for identifier in TEAMSHIFTS_ONLY:
        assert identifier in available


@pytest.mark.django_db
def test_teamshifts_emails_render_plugin_and_shared_placeholders(event, user):
    user.fullname = "Jane Volunteer"
    user.save()
    event.settings.contact_mail = "team@example.com"
    with scopes_disabled():
        context = get_email_context(event=event, user=user, **{MAIL_CONTEXT_KEY: True})

    assert context["full_name"] == "Jane Volunteer"
    assert str(context["event_name"]) == "Test Event"
    assert context["organizer_name"] == "Test Organizer"
    assert str(context["event_url"]).endswith("/test-organizer/test-event/")
    assert str(context["event_dates"]) == event.get_date_range_display()
    assert str(context["event_start_date"]) == date_format(event.date_from.astimezone(ZoneInfo(event.settings.timezone)), "DATE_FORMAT")
    assert context["event_contact_email"] == "team@example.com"


@pytest.mark.django_db
@pytest.mark.parametrize("role", EmailTemplateRoles.values)
def test_each_template_lists_every_placeholder_its_default_uses(event, role):
    with scopes_disabled():
        names = placeholder_names(event, mail_context(role))

    for text in get_default_template(role):
        assert get_used_placeholders(str(text)) <= set(names)


@pytest.mark.django_db
def test_voucher_placeholders_are_only_offered_in_the_voucher_template(event):
    with scopes_disabled():
        voucher_names = placeholder_names(event, mail_context(EmailTemplateRoles.VOUCHER_SENT))
        compose_names = placeholder_names(event, COMPOSE_CONTEXT)
        accepted_names = placeholder_names(event, mail_context(EmailTemplateRoles.APPLICATION_ACCEPTED))

    assert {"voucher_code", "voucher_redeem_url"} <= set(voucher_names)
    assert not {"voucher_code", "voucher_redeem_url", "shift_name", "role_name"} & set(compose_names)
    assert not {"voucher_code", "voucher_redeem_url", "shift_name"} & set(accepted_names)


@pytest.mark.django_db
def test_composer_lists_shared_event_and_volunteer_placeholders(event):
    with scopes_disabled():
        names = placeholder_names(event, COMPOSE_CONTEXT)

    assert "full_name" in names
    for placeholder in shared_event_placeholders():
        assert placeholder.identifier in names

import importlib

import pytest
from django.apps import apps
from django_scopes import scope
from i18nfield.strings import LazyI18nString

from teamshifts.mail.default_templates import get_default_template
from teamshifts.models import EmailTemplateRoles, TeamShiftsEmailTemplate

migration_0037 = importlib.import_module("teamshifts.migrations.0037_update_stock_email_templates_terminology")
migrate_stock_templates = migration_0037.migrate_stock_templates
reverse_migrate_stock_templates = migration_0037.reverse_migrate_stock_templates


@pytest.mark.parametrize("role", EmailTemplateRoles.values)
def test_all_default_email_templates_have_teamshifts_prefix_and_event_context(role):
    subject, body = get_default_template(role)
    subject_str = str(subject)
    body_str = str(body)

    assert subject_str.startswith("Team shifts:"), f"Subject for {role} should start with 'Team shifts:', got: {subject_str}"
    assert "{event_name}" in subject_str, f"Subject for {role} must contain '{{event_name}}'"
    assert body_str.strip() != "", f"Body for {role} should not be empty"


def test_new_application_organizer_template_clarity():
    subject, body = get_default_template(EmailTemplateRoles.NEW_APPLICATION_ORGANIZER)
    subject_str = str(subject)
    body_str = str(body)

    assert subject_str == "Team shifts: New team member application — {event_name}"
    assert "A new application has been submitted to join the event team through Team Shifts for {event_name}." in body_str
    assert "{applicant_name}" in body_str
    assert "{submitted_at}" in body_str
    assert "{pending_count}" in body_str
    assert "{application_url}" in body_str


def test_applicant_lifecycle_templates_mention_team_shifts():
    lifecycle_roles = [
        EmailTemplateRoles.APPLICATION_RECEIVED,
        EmailTemplateRoles.APPLICATION_ACCEPTED,
        EmailTemplateRoles.APPLICATION_REJECTED,
        EmailTemplateRoles.MEMBER_ADDED_BY_ORGANIZER,
        EmailTemplateRoles.MEMBER_REMOVED,
    ]

    for role in lifecycle_roles:
        subject, body = get_default_template(role)
        assert "Team Shifts" in str(body), f"Template {role} body should explicitly reference 'Team Shifts'"


def test_shift_dropped_organizer_template_clarity():
    subject, body = get_default_template(EmailTemplateRoles.SHIFT_DROPPED_ORGANIZER)
    subject_str = str(subject)
    body_str = str(body)

    assert subject_str == "Team shifts: A team member dropped a shift — {event_name}"
    assert "A Team Shifts team member has dropped a shift at {event_name}." in body_str
    assert "{applicant_name}" in body_str
    assert "{application_url}" in body_str


@pytest.mark.django_db
def test_migration_0037_migrates_stock_and_leaves_custom_templates(event):
    with scope(event=event):
        stock_template = TeamShiftsEmailTemplate.objects.create(
            event=event,
            role=EmailTemplateRoles.APPLICATION_RECEIVED,
            subject=LazyI18nString(
                {
                    "en": "We received your application — {event_name}",
                    "de": "We received your application — {event_name}",
                }
            ),
            body=LazyI18nString(
                {
                    "en": (
                        "Hi {full_name},\n\n"
                        "Thanks for applying to join the team for {event_name}. "
                        "We have received your application and will get back to you soon.\n\n"
                        "Best regards,\n"
                        "The {event_name} team"
                    )
                }
            ),
        )
        custom_template = TeamShiftsEmailTemplate.objects.create(
            event=event,
            role=EmailTemplateRoles.APPLICATION_ACCEPTED,
            subject=LazyI18nString({"en": "Custom Accepted Subject — {event_name}"}),
            body=LazyI18nString({"en": "Custom Accepted Body for {event_name}"}),
        )

    # Run forward migration
    migrate_stock_templates(apps, None)

    stock_template.refresh_from_db()
    custom_template.refresh_from_db()

    assert stock_template.subject.data["en"] == "Team shifts: We received your application — {event_name}"
    assert stock_template.subject.data["de"] == "Team shifts: We received your application — {event_name}"
    assert "join the event team through Team Shifts for {event_name}" in stock_template.body.data["en"]

    assert custom_template.subject.data["en"] == "Custom Accepted Subject — {event_name}"
    assert custom_template.body.data["en"] == "Custom Accepted Body for {event_name}"

    # Run reverse migration
    reverse_migrate_stock_templates(apps, None)

    stock_template.refresh_from_db()
    custom_template.refresh_from_db()

    assert stock_template.subject.data["en"] == "We received your application — {event_name}"
    assert stock_template.subject.data["de"] == "We received your application — {event_name}"
    assert "join the team for {event_name}" in stock_template.body.data["en"]

    assert custom_template.subject.data["en"] == "Custom Accepted Subject — {event_name}"
    assert custom_template.body.data["en"] == "Custom Accepted Body for {event_name}"

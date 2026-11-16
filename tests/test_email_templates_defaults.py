import importlib

import pytest
from django.apps import apps
from django_scopes import scope
from i18nfield.strings import LazyI18nString

from teamshifts.mail.default_templates import get_default_template
from teamshifts.models import EmailTemplateRoles, TeamShiftsEmailTemplate

migration_0038 = importlib.import_module("teamshifts.migrations.0038_update_stock_email_templates_terminology")
migrate_stock_templates = migration_0038.migrate_stock_templates
reverse_migrate_stock_templates = migration_0038.reverse_migrate_stock_templates


@pytest.mark.parametrize("role", EmailTemplateRoles.values)
def test_all_default_email_templates_have_teamshifts_prefix_and_event_context(role):
    subject, body = get_default_template(role)
    subject_str = str(subject)
    body_str = str(body)

    assert subject_str.startswith("Team Shifts:"), f"Subject for {role} should start with 'Team Shifts:', got: {subject_str}"
    assert "{event_name}" in subject_str, f"Subject for {role} must contain '{{event_name}}'"
    assert body_str.strip() != "", f"Body for {role} should not be empty"


def test_new_application_organizer_template_clarity():
    subject, body = get_default_template(EmailTemplateRoles.NEW_APPLICATION_ORGANIZER)
    subject_str = str(subject)
    body_str = str(body)

    assert subject_str == "Team Shifts: New team member application — {event_name}"
    assert "A new application has been submitted to join the {event_name} event team through Team Shifts." in body_str
    assert "{applicant_name}" in body_str
    assert "{submitted_at}" in body_str
    assert "{pending_count}" in body_str
    assert "{application_url}" in body_str


def test_applicant_lifecycle_templates_clarity():
    received_subject, received_body = get_default_template(EmailTemplateRoles.APPLICATION_RECEIVED)
    assert str(received_subject) == "Team Shifts: We received your application — {event_name}"
    assert "Thanks for applying to join the {event_name} event team through Team Shifts." in str(received_body)

    accepted_subject, accepted_body = get_default_template(EmailTemplateRoles.APPLICATION_ACCEPTED)
    assert str(accepted_subject) == "Team Shifts: Your application was accepted — {event_name}"
    assert "Great news — your application to join the {event_name} event team through Team Shifts has been accepted. Welcome to the team!" in str(accepted_body)

    rejected_subject, rejected_body = get_default_template(EmailTemplateRoles.APPLICATION_REJECTED)
    assert str(rejected_subject) == "Team Shifts: Update on your application — {event_name}"
    assert "Thank you for your interest in joining the {event_name} event team through Team Shifts." in str(rejected_body)

    added_subject, added_body = get_default_template(EmailTemplateRoles.MEMBER_ADDED_BY_ORGANIZER)
    assert str(added_subject) == "Team Shifts: You have been added to the team — {event_name}"
    assert "volunteer" not in str(added_subject).lower()
    assert "You have been added to the {event_name} event team through Team Shifts by the event organizer." in str(added_body)

    removed_subject, removed_body = get_default_template(EmailTemplateRoles.MEMBER_REMOVED)
    assert str(removed_subject) == "Team Shifts: Update on your participation — {event_name}"
    assert (
        "We wanted to let you know that you are no longer part of the {event_name} event team through Team Shifts, "
        "and you have been removed from any upcoming shifts." in str(removed_body)
    )

    lifecycle_roles = [
        EmailTemplateRoles.APPLICATION_RECEIVED,
        EmailTemplateRoles.APPLICATION_ACCEPTED,
        EmailTemplateRoles.APPLICATION_REJECTED,
        EmailTemplateRoles.MEMBER_ADDED_BY_ORGANIZER,
        EmailTemplateRoles.MEMBER_REMOVED,
    ]
    for role in lifecycle_roles:
        _, body = get_default_template(role)
        assert "through Team Shifts" in str(body)


def test_shift_dropped_organizer_template_clarity():
    subject, body = get_default_template(EmailTemplateRoles.SHIFT_DROPPED_ORGANIZER)
    subject_str = str(subject)
    body_str = str(body)

    assert subject_str == "Team Shifts: A team member dropped a shift — {event_name}"
    assert "A team member has dropped a shift at {event_name}." in body_str
    assert "through Team Shifts" not in body_str
    assert "{applicant_name}" in body_str
    assert "{application_url}" in body_str


def test_shift_reminder_template_clarity():
    subject, body = get_default_template(EmailTemplateRoles.SHIFT_REMINDER)
    assert str(subject) == "Team Shifts: Your shift overview for {event_name}"


@pytest.mark.django_db
def test_migration_0038_migrates_stock_and_leaves_custom_templates(event):
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
        organizer_template = TeamShiftsEmailTemplate.objects.create(
            event=event,
            role=EmailTemplateRoles.NEW_APPLICATION_ORGANIZER,
            subject=LazyI18nString({"en": "New team member application — {event_name}"}),
            body=LazyI18nString(
                {
                    "en": (
                        "A new team member application has been submitted for {event_name}.\n\n"
                        "Applicant: {applicant_name}\n\n"
                        "Submitted: {submitted_at}\n\n"
                        "Pending applications: {pending_count}\n\n"
                        "Review the application here:\n"
                        "{application_url}\n\n"
                        "The {event_name} team"
                    )
                }
            ),
        )

    # Run forward migration
    migrate_stock_templates(apps, None)

    stock_template.refresh_from_db()
    custom_template.refresh_from_db()
    organizer_template.refresh_from_db()

    assert stock_template.subject.data["en"] == "Team Shifts: We received your application — {event_name}"
    assert stock_template.subject.data["de"] == "Team Shifts: We received your application — {event_name}"
    assert "join the {event_name} event team through Team Shifts." in stock_template.body.data["en"]

    assert custom_template.subject.data["en"] == "Custom Accepted Subject — {event_name}"
    assert custom_template.body.data["en"] == "Custom Accepted Body for {event_name}"

    assert organizer_template.subject.data["en"] == "Team Shifts: New team member application — {event_name}"
    assert "join the {event_name} event team through Team Shifts." in organizer_template.body.data["en"]

    # Run reverse migration
    reverse_migrate_stock_templates(apps, None)

    stock_template.refresh_from_db()
    custom_template.refresh_from_db()
    organizer_template.refresh_from_db()

    assert stock_template.subject.data["en"] == "We received your application — {event_name}"
    assert stock_template.subject.data["de"] == "We received your application — {event_name}"
    assert "join the team for {event_name}" in stock_template.body.data["en"]

    assert custom_template.subject.data["en"] == "Custom Accepted Subject — {event_name}"
    assert custom_template.body.data["en"] == "Custom Accepted Body for {event_name}"

    assert organizer_template.subject.data["en"] == "New team member application — {event_name}"
    assert "A new team member application has been submitted for {event_name}." in organizer_template.body.data["en"]

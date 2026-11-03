import pytest

from teamshifts.mail.default_templates import get_default_template
from teamshifts.models import EmailTemplateRoles


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

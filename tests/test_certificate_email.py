"""Tests for certificate email notification on first generation."""

from unittest.mock import patch

import pytest
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import User

from teamshifts.mail.default_templates import get_default_template
from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    CertificateSettings,
    EmailTemplateRoles,
    MemberCertificate,
    TeamMemberApplication,
)
from teamshifts.services.certificates import _send_certificate_email, generate_certificate


@pytest.fixture
def member(db):
    return User.objects.create_user(
        email="volunteer@example.com",
        password="secret",
        fullname="Jane Volunteer",
        locale="en",
    )


@pytest.fixture
def application(event, member):
    with scope(event=event):
        CallForTeamMembers.objects.get_or_create(event=event)
        return TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
        )


@pytest.fixture
def cert_settings(event):
    with scope(event=event):
        return CertificateSettings.objects.create(
            event=event,
            require_arrived=False,
            require_min_shifts=False,
        )


@pytest.mark.django_db
def test_send_certificate_email_calls_mail(event, application, cert_settings):
    with scope(event=event):
        MemberCertificate.objects.get_or_create(application=application)

    with patch("teamshifts.services.certificates.mail") as mock_mail:
        with scope(event=event):
            _send_certificate_email(application, b"%PDF-1.4 fake content")

    mock_mail.assert_called_once()
    call_kwargs = mock_mail.call_args.kwargs
    assert call_kwargs["email"] == application.user.email
    assert call_kwargs["event"] == event
    assert call_kwargs["user"] == application.user
    assert call_kwargs["attach_cached_files"]


@pytest.mark.django_db
def test_send_certificate_email_stamps_notified_at(event, application, cert_settings):
    captured = []
    with patch("teamshifts.services.certificates.transaction.on_commit", side_effect=lambda fn: captured.append(fn)):
        with scope(event=event, organizer=event.organizer):
            generate_certificate(application, cert_settings)

    with scope(event=event):
        cert = MemberCertificate.objects.get(application=application)
    assert cert.notified_at is not None
    assert len(captured) == 1


@pytest.mark.django_db
def test_send_skipped_when_no_email(event, application, cert_settings):
    application.user.email = ""
    application.user.save(update_fields=["email"])

    with patch("teamshifts.services.certificates.mail") as mock_mail:
        with scope(event=event):
            _send_certificate_email(application, b"%PDF-fake")

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_send_skipped_when_no_cfm(event, application):
    from teamshifts.models import CallForTeamMembers

    with (
        patch.object(
            application.event.__class__,
            "call_for_team_members",
            new_callable=lambda: property(lambda _: (_ for _ in ()).throw(CallForTeamMembers.DoesNotExist)),
        ),
        patch("teamshifts.services.certificates.mail") as mock_mail,
    ):
        _send_certificate_email(application, b"%PDF-fake")

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_generate_certificate_registers_email_on_first_gen(event, application, cert_settings):
    captured = []
    with patch("teamshifts.services.certificates.transaction.on_commit", side_effect=lambda fn: captured.append(fn)):
        with scope(event=event, organizer=event.organizer):
            generate_certificate(application, cert_settings)

    with scope(event=event):
        cert = MemberCertificate.objects.get(application=application)
    assert len(captured) == 1
    assert cert.notified_at is not None


@pytest.mark.django_db
def test_generate_certificate_skips_email_on_regen(event, application, cert_settings):
    with scope(event=event):
        cert, _ = MemberCertificate.objects.get_or_create(application=application)
        cert.notified_at = now()
        cert.save(update_fields=["notified_at"])

    captured = []
    with patch("teamshifts.services.certificates.transaction.on_commit", side_effect=lambda fn: captured.append(fn)):
        with scope(event=event, organizer=event.organizer):
            generate_certificate(application, cert_settings)

    assert len(captured) == 0


@pytest.mark.django_db
def test_certificate_generated_role_value():
    assert EmailTemplateRoles.CERTIFICATE_GENERATED == "teamshifts.certificate.generated"


@pytest.mark.django_db
def test_default_template_resolves_for_certificate():
    subject, body = get_default_template(EmailTemplateRoles.CERTIFICATE_GENERATED)
    assert subject is not None
    body_str = str(body)
    assert "{full_name}" in body_str
    assert "{event_name}" in body_str


@pytest.mark.django_db
def test_get_mail_template_auto_creates_certificate_template(event, application):
    with scope(event=event):
        cfm, _ = CallForTeamMembers.objects.get_or_create(event=event)
        template = cfm.get_mail_template(EmailTemplateRoles.CERTIFICATE_GENERATED)

    assert template.role == EmailTemplateRoles.CERTIFICATE_GENERATED
    assert template.subject is not None
    assert template.body is not None

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


# ---------------------------------------------------------------------------
# _send_certificate_email unit tests
#
# We test the helper directly (not via generate_certificate) because
# transaction.on_commit never fires inside non-transactional django_db tests.
# mail_send_task is imported *inside* the function so it must be patched at
# its source path: eventyay.base.services.mail.mail_send_task.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_certificate_email_calls_mail_task(event, application, cert_settings):
    """_send_certificate_email dispatches mail_send_task with the PDF attached."""
    with scope(event=event):
        MemberCertificate.objects.get_or_create(application=application)

    pdf_bytes = b"%PDF-1.4 fake content"

    with patch("eventyay.base.services.mail.mail_send_task") as mock_task:
        with scope(event=event):
            _send_certificate_email(application, pdf_bytes)

    mock_task.apply_async.assert_called_once()
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]

    assert kwargs["to"] == [application.user.email]
    assert kwargs["attach_file_name"] == f"{event.slug}-jane-volunteer.pdf"
    assert kwargs["attach_file_base64"]  # non-empty base64 string
    assert kwargs["event"] == event.pk


@pytest.mark.django_db
def test_send_certificate_email_stamps_notified_at(event, application, cert_settings):
    """_send_certificate_email sets notified_at after dispatching the task."""
    with scope(event=event):
        MemberCertificate.objects.get_or_create(application=application)

    with patch("eventyay.base.services.mail.mail_send_task"):
        with scope(event=event):
            _send_certificate_email(application, b"%PDF-fake")

    with scope(event=event):
        cert = MemberCertificate.objects.get(application=application)
    assert cert.notified_at is not None


@pytest.mark.django_db
def test_send_skipped_when_no_email(event, application, cert_settings):
    """_send_certificate_email is a no-op when the user has no email address."""
    application.user.email = ""
    application.user.save(update_fields=["email"])

    with patch("eventyay.base.services.mail.mail_send_task") as mock_task:
        with scope(event=event):
            _send_certificate_email(application, b"%PDF-fake")

    mock_task.apply_async.assert_not_called()


@pytest.mark.django_db
def test_send_skipped_when_no_cfm(event, application):
    """_send_certificate_email skips sending when cfm lookup raises any exception."""
    # Patch the cfm lookup to raise, simulating a missing CallForTeamMembers
    with (
        patch.object(
            application.event.__class__,
            "call_for_team_members",
            new_callable=lambda: property(lambda _: (_ for _ in ()).throw(Exception("no cfm"))),
        ),
        patch("eventyay.base.services.mail.mail_send_task") as mock_task,
    ):
        _send_certificate_email(application, b"%PDF-fake")

    mock_task.apply_async.assert_not_called()


# ---------------------------------------------------------------------------
# generate_certificate idempotency guard
#
# We patch transaction.on_commit to capture (but not execute) the callback,
# so we can assert it was registered exactly once on first generation and
# zero times on re-generation.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_generate_certificate_registers_email_on_first_gen(event, application, cert_settings):
    """generate_certificate registers one on_commit callback on first generation."""
    captured = []
    with patch("teamshifts.services.certificates.transaction.on_commit", side_effect=lambda fn: captured.append(fn)):
        with scope(event=event):
            generate_certificate(application, cert_settings)

    with scope(event=event):
        cert = MemberCertificate.objects.get(application=application)

    assert len(captured) == 1
    # notified_at is still None — email fires on_commit in production
    assert cert.notified_at is None


@pytest.mark.django_db
def test_generate_certificate_skips_email_on_regen(event, application, cert_settings):
    """generate_certificate does NOT register on_commit when notified_at is already set."""
    with scope(event=event):
        cert, _ = MemberCertificate.objects.get_or_create(application=application)
        cert.notified_at = now()
        cert.save(update_fields=["notified_at"])

    captured = []
    with patch("teamshifts.services.certificates.transaction.on_commit", side_effect=lambda fn: captured.append(fn)):
        with scope(event=event):
            generate_certificate(application, cert_settings)

    assert len(captured) == 0


# ---------------------------------------------------------------------------
# Template and role resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_certificate_generated_role_value():
    """EmailTemplateRoles.CERTIFICATE_GENERATED has the expected string value."""
    assert EmailTemplateRoles.CERTIFICATE_GENERATED == "teamshifts.certificate.generated"


@pytest.mark.django_db
def test_default_template_resolves_for_certificate():
    """get_default_template returns a non-empty subject/body for CERTIFICATE_GENERATED."""
    subject, body = get_default_template(EmailTemplateRoles.CERTIFICATE_GENERATED)
    assert subject is not None
    body_str = str(body)
    assert "{full_name}" in body_str
    assert "{event_name}" in body_str


@pytest.mark.django_db
def test_get_mail_template_auto_creates_certificate_template(event, application):
    """cfm.get_mail_template auto-creates the certificate template from defaults."""
    with scope(event=event):
        cfm, _ = CallForTeamMembers.objects.get_or_create(event=event)
        template = cfm.get_mail_template(EmailTemplateRoles.CERTIFICATE_GENERATED)

    assert template.role == EmailTemplateRoles.CERTIFICATE_GENERATED
    assert template.subject is not None
    assert template.body is not None

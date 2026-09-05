from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django_scopes import scope
from i18nfield.strings import LazyI18nString

from teamshifts.forms import EmailComposeForm, EmailTemplateForm, sanitize_i18n_email_html
from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueueRecipient,
    TeamShiftsEmailTemplate,
)
from teamshifts.services.email import html_to_plain_text, looks_like_email_html, queue_lifecycle_email
from teamshifts.signals import teamshifts_email_plain_text_body
from teamshifts.tasks import send_queued_email

TIPTAP_BODY = (
    "<p>Hi {full_name},</p>"
    "<p>Thank you for your interest in joining the {event_name} team.</p>"
    "<p></p>"
    "<p>Unfortunately, we are unable to accept your application at this time.</p>"
    "<p>We appreciate your enthusiasm and hope you enjoy the event.</p>"
    "<p>Best regards,</p>"
    "<p>The {event_name} team</p>"
)


@pytest.fixture
def call_for_team_members(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True)


@pytest.fixture
def role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Volunteer")


@pytest.fixture
def applicant(event, role, django_user_model):
    user = django_user_model.objects.create_user(
        email="velia@example.com",
        password="x",
        fullname="Velia",
        locale="en",
    )
    with scope(event=event):
        TeamMemberApplication.objects.create(
            event=event,
            user=user,
            status=ApplicationStatus.PENDING,
        )
    return user


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", False),
        ("Hi there", False),
        ("Hello <b>world</b>", True),
        ("<p>Hello</p>", True),
        ("3 < 4 and 5 > 2", False),
    ],
)
def test_looks_like_email_html(value, expected):
    assert looks_like_email_html(value) is expected


def test_html_to_plain_text_preserves_paragraphs_without_tags():
    plain = html_to_plain_text(TIPTAP_BODY)
    assert "<p>" not in plain
    assert "</p>" not in plain
    assert "&lt;" not in plain
    assert "Hi {full_name}," in plain
    assert "Thank you for your interest" in plain
    assert "Best regards," in plain
    # Paragraph boundaries should not collapse into a single line.
    assert "\n" in plain


def test_looks_like_email_html_detects_headings_only():
    """Issue #116 regresion: a body containing only heading tags must be
    detected as HTML so the plain-text MIME part is stripped."""
    assert looks_like_email_html("<h1>Title</h1><h2>Subtitle</h2>") is True
    assert "<p>" not in html_to_plain_text("<h1>Title</h1><h2>Subtitle</h2>")


def test_sanitize_i18n_email_html_strips_unsafe_tags():
    dirty = LazyI18nString({"en": '<p>Hello</p><script>alert(1)</script><p onclick="x">World</p>'})
    cleaned = sanitize_i18n_email_html(dirty)
    text = str(cleaned)
    assert "<script>" not in text
    assert "onclick" not in text
    assert "Hello" in text
    assert "World" in text


def _i18n_post(locales, **fields_by_name):
    """Build POST data for i18nfield widgets (name_0, name_1, … per locale order)."""
    data = {}
    for name, value in fields_by_name.items():
        for index, _locale in enumerate(locales):
            data[f"{name}_{index}"] = value
    return data


@pytest.mark.django_db
def test_email_template_form_sanitizes_tiptap_html_on_save(event, call_for_team_members):
    locales = list(event.settings.locales) or ["en"]
    locale = locales[0]
    with scope(event=event):
        template = TeamShiftsEmailTemplate.objects.create(
            event=event,
            role=EmailTemplateRoles.APPLICATION_REJECTED,
            subject=LazyI18nString({locale: "Update"}),
            body=LazyI18nString({locale: "plain"}),
        )

    form = EmailTemplateForm(
        data=_i18n_post(
            locales,
            subject="Update on your application",
            body=TIPTAP_BODY + '<script>alert("x")</script>',
        ),
        instance=template,
        locales=locales,
    )
    assert form.is_valid(), form.errors
    with scope(event=event):
        saved = form.save()
        saved.refresh_from_db()
        body = str(saved.body)

    assert "<p>Hi {full_name},</p>" in body
    assert "<script>" not in body
    assert "alert" not in body


@pytest.mark.django_db
def test_email_compose_form_uses_sanitizing_body_field(event):
    locales = list(event.settings.locales) or ["en"]
    form = EmailComposeForm(
        data={
            **_i18n_post(locales, subject="Hello", message="<p>Hi</p><script>alert(1)</script>"),
            "status": ApplicationStatus.ACCEPTED,
        },
        event=event,
    )
    assert form.is_valid(), form.errors
    message = str(form.cleaned_data["message"])
    assert "<p>Hi</p>" in message
    assert "<script>" not in message


def test_email_filter_strips_html_from_plain_mime_part():
    raw_html = TIPTAP_BODY.replace("{full_name}", "Velia").replace("{event_name}", "Summit")
    message = SimpleNamespace(body=raw_html)

    filtered = teamshifts_email_plain_text_body(sender=None, message=message)

    assert filtered is message
    assert filtered.body == html_to_plain_text(raw_html)
    assert "<p>" not in filtered.body
    assert "Hi Velia," in filtered.body
    assert "Best regards," in filtered.body


@pytest.mark.django_db
def test_lifecycle_email_with_tiptap_template_renders_html_and_plain(event, call_for_team_members, applicant, role):
    """Regression for #116: edited Tiptap HTML templates must not leak raw tags."""
    locales = list(event.settings.locales) or ["en"]
    locale = locales[0]
    with scope(event=event):
        TeamShiftsEmailTemplate.objects.update_or_create(
            event=event,
            role=EmailTemplateRoles.APPLICATION_REJECTED,
            defaults={
                "subject": LazyI18nString({locale: "Update on your application"}),
                "body": LazyI18nString({locale: TIPTAP_BODY}),
            },
        )
        application = TeamMemberApplication.objects.get(user=applicant)

    # Queue without dispatching so we can assert on the stored template and
    # control the send path (eager Celery would otherwise send immediately).
    with patch("teamshifts.services.email._dispatch"):
        queue = queue_lifecycle_email(application, EmailTemplateRoles.APPLICATION_REJECTED)

    assert queue is not None
    with scope(event=event):
        queue.refresh_from_db()
        stored = str(queue.message)
    assert "<p>Hi {full_name},</p>" in stored

    plain = html_to_plain_text(
        stored.replace("{full_name}", "Velia").replace("{event_name}", str(event.name)),
    )
    assert "<p>" not in plain
    assert "</p>" not in plain
    assert "Hi Velia," in plain
    assert "Unfortunately, we are unable to accept" in plain

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    mock_mail.assert_called_once()
    kwargs = mock_mail.call_args.kwargs
    sent_template = str(kwargs["template"])
    assert "<p>Hi {full_name},</p>" in sent_template
    assert "&lt;p&gt;" not in sent_template

    # Plain MIME conversion used by email_filter must not leave tags.
    filtered_body = html_to_plain_text(sent_template.format(full_name="Velia", event_name=str(event.name), role_name="Volunteer"))
    assert "<p>" not in filtered_body
    assert "Hi Velia," in filtered_body

    with scope(event=event):
        queue.refresh_from_db()
        recipient = TeamShiftsEmailQueueRecipient.objects.get(queue=queue)
    assert queue.sent_at is not None
    assert recipient.sent_at is not None

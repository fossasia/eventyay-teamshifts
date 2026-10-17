from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User
from i18nfield.strings import LazyI18nString

from teamshifts.models import (
    CallForTeamMembers,
    EmailTemplateRoles,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from teamshifts.services.email import queue_email, record_sent_email
from teamshifts.services.vouchers import _send_voucher_email
from teamshifts.tasks import render_subject


@pytest.fixture(autouse=True)
def site_url(settings):
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def organizer_user(event, user):
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Organizers",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    return user


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True)


@pytest.fixture
def volunteer(db):
    return User.objects.create_user(email="volunteer@example.com", password="x", fullname="Vera Volunteer")


def _create_sent(event, volunteer, subject, mail_type="", author=None):
    sent_now = now()
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=author,
            subject=subject,
            message="body",
            locale="en",
            mail_type=mail_type,
            sent_at=sent_now,
        )
        TeamShiftsEmailQueueRecipient.objects.create(
            queue=queue,
            user=volunteer,
            email=volunteer.email,
            subject=subject,
            sent_at=sent_now,
        )
    return queue


def _sent_url(event):
    return reverse("plugins:teamshifts:email_sent", kwargs={"organizer": event.organizer.slug, "event": event.slug})


@pytest.mark.django_db
def test_queue_email_stores_mail_type(event, volunteer):
    queue = queue_email(
        event=event,
        subject="s",
        message="m",
        recipients=[volunteer],
        mail_type=EmailTemplateRoles.APPLICATION_ACCEPTED,
        dispatch=False,
    )
    assert queue.mail_type == EmailTemplateRoles.APPLICATION_ACCEPTED
    assert queue.mail_type_label == "Application accepted"


@pytest.mark.django_db
def test_mail_type_label_defaults_to_custom(event):
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.create(event=event, subject="s", message="m")
    assert queue.mail_type_label == "Custom"


@pytest.mark.django_db
def test_record_sent_email_stores_rendered_subject(event, volunteer):
    subject = LazyI18nString({"en": "Your ticket voucher — {event_name}"})
    queue = record_sent_email(
        event,
        volunteer,
        subject,
        "body",
        {"event_name": "Test Event"},
        "en",
        EmailTemplateRoles.VOUCHER_SENT,
    )
    with scope(event=event):
        recipient = queue.recipients.get()
    assert recipient.subject == "Your ticket voucher — Test Event"
    assert recipient.sent_at is not None
    assert queue.mail_type == EmailTemplateRoles.VOUCHER_SENT


@pytest.mark.django_db
def test_sent_list_shows_one_row_per_recipient_with_type_and_timezone(client, event, cfm, organizer_user, volunteer):
    other = User.objects.create_user(email="other@example.com", password="x", fullname="Otto Other")
    queue = _create_sent(event, volunteer, "Welcome to Test Event", EmailTemplateRoles.APPLICATION_RECEIVED)
    with scope(event=event):
        TeamShiftsEmailQueueRecipient.objects.create(queue=queue, user=other, email=other.email, subject="Welcome to Test Event", sent_at=now())
    client.force_login(organizer_user)

    response = client.get(_sent_url(event))

    assert response.status_code == 200
    content = response.content.decode()
    assert content.count("Welcome to Test Event") == 2
    assert "Vera Volunteer" in content
    assert "Otto Other" in content
    assert "Application received" in content
    assert str(event.timezone) in content
    assert "All roles" not in content


@pytest.mark.django_db
def test_sent_list_labels_organizer_composed_email_as_custom(client, event, cfm, organizer_user, volunteer):
    _create_sent(event, volunteer, "Hello team", author=organizer_user)
    client.force_login(organizer_user)

    response = client.get(_sent_url(event))

    assert "Custom" in response.content.decode()


@pytest.mark.django_db
def test_sent_list_includes_lifecycle_emails_without_author(client, event, cfm, organizer_user, volunteer):
    _create_sent(event, volunteer, "Accepted!", EmailTemplateRoles.APPLICATION_ACCEPTED)
    client.force_login(organizer_user)

    response = client.get(_sent_url(event))

    assert "Accepted!" in response.content.decode()


@pytest.mark.django_db
def test_sent_list_hides_untyped_legacy_rows_without_author(client, event, cfm, organizer_user, volunteer):
    _create_sent(event, volunteer, "Legacy lifecycle mail")
    client.force_login(organizer_user)

    response = client.get(_sent_url(event))

    assert "Legacy lifecycle mail" not in response.content.decode()


@pytest.mark.django_db
def test_sent_list_search_matches_subject_and_recipient_name(client, event, cfm, organizer_user, volunteer):
    other = User.objects.create_user(email="other@example.com", password="x", fullname="Otto Other")
    _create_sent(event, volunteer, "Shift reminder", EmailTemplateRoles.SHIFT_ASSIGNED_BY_ORGANIZER)
    _create_sent(event, other, "Voucher inside", EmailTemplateRoles.VOUCHER_SENT)
    client.force_login(organizer_user)

    by_subject = client.get(_sent_url(event), {"q": "reminder"}).content.decode()
    by_name = client.get(_sent_url(event), {"q": "Otto"}).content.decode()

    assert "Shift reminder" in by_subject
    assert "Voucher inside" not in by_subject
    assert "Voucher inside" in by_name
    assert "Shift reminder" not in by_name


@pytest.mark.django_db
def test_sent_list_shows_email_to_coordinator(client, event, cfm, organizer_user, volunteer):
    _create_sent(event, volunteer, "Hello", EmailTemplateRoles.VOUCHER_SENT)
    client.force_login(organizer_user)

    response = client.get(_sent_url(event))

    assert volunteer.email in response.content.decode()


@pytest.mark.django_db
def test_sent_list_hides_email_from_lead_with_hidden_emails(client, event, cfm, volunteer):
    lead = User.objects.create_user(email="lead@example.com", password="x")
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Leads",
            all_events=True,
            teamshifts_role="lead",
            hide_teamshifts_emails=True,
        )
        team.members.add(lead)
    _create_sent(event, volunteer, "Hello", EmailTemplateRoles.VOUCHER_SENT)
    client.force_login(lead)

    listed = client.get(_sent_url(event))
    searched = client.get(_sent_url(event), {"q": "example.com"})

    assert listed.status_code == 200
    assert "Vera Volunteer" in listed.content.decode()
    assert volunteer.email not in listed.content.decode()
    assert "Vera Volunteer" not in searched.content.decode()


@pytest.mark.django_db
def test_render_subject_applies_event_mail_prefix(event):
    event.settings.mail_prefix = "[Summit]"

    assert render_subject("Hello {name}", {"name": "Vera"}, "en", event) == "[Summit] Hello Vera"
    assert render_subject("Hello {name}", {"name": "Vera"}, "en") == "Hello Vera"


@pytest.mark.django_db
def test_sent_list_includes_recipients_of_partially_sent_queue(client, event, cfm, organizer_user, volunteer):
    other = User.objects.create_user(email="other@example.com", password="x", fullname="Otto Other")
    queue = _create_sent(event, volunteer, "Partial", author=organizer_user)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(pk=queue.pk).update(sent_at=None)
        TeamShiftsEmailQueueRecipient.objects.create(queue=queue, user=other, email=other.email, error="boom")
    client.force_login(organizer_user)

    content = client.get(_sent_url(event)).content.decode()

    assert "Vera Volunteer" in content
    assert "Otto Other" not in content


@pytest.mark.django_db
def test_voucher_credentials_are_not_stored_in_listed_subject(event, volunteer):
    voucher = type("FakeVoucher", (), {"code": "SECRETCODE"})()
    template = type(
        "FakeTemplate",
        (),
        {
            "subject": LazyI18nString({"en": "Voucher {voucher_code} for {event_name}"}),
            "body": LazyI18nString({"en": "Body {ticket_claim_url}"}),
        },
    )()

    with patch("teamshifts.services.vouchers.mail"), patch("teamshifts.services.vouchers.build_absolute_uri", return_value="https://x.test/redeem"):
        assert _send_voucher_email(event, volunteer, voucher, template, "en")

    with scope(event=event):
        stored = TeamShiftsEmailQueueRecipient.objects.get(email=volunteer.email).subject
    assert "SECRETCODE" not in stored
    assert "[redacted]" in stored
    assert str(event.name) in stored


@pytest.mark.django_db
def test_sent_list_shows_placeholder_for_blank_subject(client, event, cfm, organizer_user, volunteer):
    _create_sent(event, volunteer, "", author=organizer_user)
    client.force_login(organizer_user)

    response = client.get(_sent_url(event))

    assert "(No subject)" in response.content.decode()

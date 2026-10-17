import datetime
import json
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
)
from teamshifts.services.email import SHIFT_SUMMARY_SEND_TIME, next_shift_summary_time, queue_shift_summary_email
from teamshifts.services.shift_summary import build_shift_summary, build_shift_summary_sample
from teamshifts.tasks import send_queued_email

JSON_HEADERS = {"HTTP_ACCEPT": "application/json", "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def site_url(settings):
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def volunteer(django_user_model):
    return django_user_model.objects.create_user(email="vol@example.com", password="x", fullname="Vol", locale="en")


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True, shift_schedule_published=True)


@pytest.fixture
def role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Greeter")


def _shift(event, name, start):
    with scope(event=event):
        return Shift.objects.create(event=event, name=name, start_time=start, end_time=start + datetime.timedelta(hours=2))


def _summary_queues(event):
    with scope(event=event):
        return list(TeamShiftsEmailQueue.objects.filter(is_shift_summary=True).order_by("pk"))


@pytest.mark.django_db
def test_first_change_queues_summary_for_next_send_time(event, cfm, volunteer):
    queue_shift_summary_email(event, volunteer)
    (queue,) = _summary_queues(event)
    assert queue.send_after.astimezone(event.tz).time() == SHIFT_SUMMARY_SEND_TIME
    assert now() < queue.send_after <= now() + datetime.timedelta(hours=24, minutes=1)


def test_send_time_is_that_evening_or_the_next_one_after_the_cutoff(event):
    tz = event.tz

    def at(day, hour, minute=0):
        return datetime.datetime(2026, 3, day, hour, minute, tzinfo=tz)

    assert next_shift_summary_time(event, at(10, 0, 30)) == at(10, 20)
    assert next_shift_summary_time(event, at(10, 9, 30)) == at(10, 20)
    assert next_shift_summary_time(event, at(10, 19, 59)) == at(10, 20)
    assert next_shift_summary_time(event, at(10, 20, 0)) == at(11, 20)
    assert next_shift_summary_time(event, at(10, 23, 30)) == at(11, 20)


@pytest.mark.django_db
def test_summaries_are_grouped_per_volunteer_per_day(event, cfm, volunteer, django_user_model):
    first = queue_shift_summary_email(event, volunteer)
    second = queue_shift_summary_email(event, volunteer)
    assert first.pk == second.pk
    assert second.send_after == first.send_after

    other = django_user_model.objects.create_user(email="o@example.com", password="x", fullname="O", locale="en")
    other_queue = queue_shift_summary_email(event, other)
    assert len(_summary_queues(event)) == 2
    with scope(event=event):
        assert [r.user_id for r in first.recipients.all()] == [volunteer.pk]
        assert [r.user_id for r in other_queue.recipients.all()] == [other.pk]

    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(pk=first.pk).update(sent_at=now())
    queue_shift_summary_email(event, volunteer)
    assert len(_summary_queues(event)) == 3


@pytest.mark.django_db
def test_overdue_unsent_summary_is_reused_until_the_sender_claims_it(event, cfm, volunteer):
    first = queue_shift_summary_email(event, volunteer)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(pk=first.pk).update(send_after=now() - datetime.timedelta(hours=1))
    assert queue_shift_summary_email(event, volunteer).pk == first.pk

    with scope(event=event):
        first.recipients.update(sent_at=now())
    assert queue_shift_summary_email(event, volunteer).pk != first.pk


@pytest.mark.django_db
def test_nothing_is_sent_while_schedule_is_unpublished(event, cfm, volunteer):
    with scope(event=event):
        CallForTeamMembers.objects.filter(pk=cfm.pk).update(shift_schedule_published=False)
    event.refresh_from_db()
    assert queue_shift_summary_email(event, volunteer) is None
    assert _summary_queues(event) == []

    with scope(event=event):
        CallForTeamMembers.objects.filter(pk=cfm.pk).update(shift_schedule_published=True)
    event.refresh_from_db()
    queue = queue_shift_summary_email(event, volunteer)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(pk=queue.pk).update(send_after=now() - datetime.timedelta(minutes=1))
        CallForTeamMembers.objects.filter(pk=cfm.pk).update(shift_schedule_published=False)
    event.refresh_from_db()
    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)
    mock_mail.assert_not_called()
    assert _summary_queues(event) == []


@pytest.mark.django_db
def test_summary_lists_current_shifts_sorted_or_links_to_schedule(event, cfm, volunteer, role):
    base = now() + datetime.timedelta(days=3)
    late = _shift(event, "Teardown", base + datetime.timedelta(days=1))
    early = _shift(event, "Setup", base)
    with scope(event=event):
        ShiftAssignment.objects.create(shift=late, team_member=volunteer, role=role)
        ShiftAssignment.objects.create(shift=early, team_member=volunteer, role=role)
    text = build_shift_summary(event, volunteer)
    assert text.index("Setup") < text.index("Teardown")
    assert "Role: Greeter" in text

    with scope(event=event):
        ShiftAssignment.objects.all().delete()
    text = build_shift_summary(event, volunteer)
    assert "no shifts" in text
    assert "/teamshifts/shifts/" in text


@pytest.mark.django_db
def test_summary_template_is_editable_role(event, cfm):
    with scope(event=event):
        template = cfm.get_mail_template(EmailTemplateRoles.SHIFT_SUMMARY)
    assert "{shift_summary}" in str(template.body)
    assert "{my_shifts_url}" in str(template.body)
    assert "{shift_schedule_url}" in str(template.body)
    assert str(template.subject) == "Your shifts for {event_name}"


@pytest.mark.django_db
def test_summary_preview_sample_uses_the_events_own_shifts(event, cfm):
    assert "no shifts" in build_shift_summary_sample(event)
    _shift(event, "Registration Desk", now() + datetime.timedelta(days=3))
    assert "Registration Desk" in build_shift_summary_sample(event)


@pytest.mark.django_db
def test_published_summary_is_sent_with_current_shifts(event, cfm, volunteer, role):
    shift = _shift(event, "Setup", now() + datetime.timedelta(days=3))
    with scope(event=event):
        ShiftAssignment.objects.create(shift=shift, team_member=volunteer, role=role)
    queue = queue_shift_summary_email(event, volunteer)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(pk=queue.pk).update(send_after=now() - datetime.timedelta(minutes=1))
    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)
    mock_mail.assert_called_once()
    context = mock_mail.call_args.kwargs["context"]
    assert "Setup" in context["shift_summary"]
    assert context["my_shifts_url"].endswith("/common/my-shifts/")

    with scope(event=event):
        ShiftAssignment.objects.all().delete()
    queue = queue_shift_summary_email(event, volunteer)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(pk=queue.pk).update(send_after=now() - datetime.timedelta(minutes=1))
    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)
    context = mock_mail.call_args.kwargs["context"]
    assert "no shifts" in context["shift_summary"]
    assert context["my_shifts_url"] == context["shift_schedule_url"]


@pytest.mark.django_db
def test_summary_uses_the_locations_display_name(event, cfm, volunteer, role):
    shift = _shift(event, "Setup", now() + datetime.timedelta(days=3))
    with scope(event=event):
        shift.location = ShiftLocation.objects.create(event=event, name="Stored name")
        shift.save()
        ShiftAssignment.objects.create(shift=shift, team_member=volunteer, role=role)
    with patch.object(ShiftLocation, "display_name", return_value="Room Alpha") as display_name:
        text = build_shift_summary(event, volunteer, "de")
    display_name.assert_called_once_with("de")
    assert "Location: Room Alpha" in text


@pytest.fixture
def application(event, cfm, volunteer):
    with scope(event=event):
        return TeamMemberApplication.objects.create(event=event, user=volunteer, status=ApplicationStatus.ACCEPTED)


@pytest.fixture
def organizer_user(event, user):
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, name="Orga", can_change_event_settings=True, all_events=True)
        team.members.add(user)
    return user


@pytest.fixture
def shift(event, role):
    shift = _shift(event, "Day shift", now() + datetime.timedelta(hours=24))
    with scope(event=event):
        ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=3)
    return shift


def _post(client, event, name, shift, payload):
    url = reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk})
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(url, data=json.dumps(payload), content_type="application/json", **JSON_HEADERS)


def _claim(client, event, shift, role, **extra):
    return _post(client, event, "public_shift_claim", shift, {"role_id": role.pk, **extra})


def _drop(client, event, shift, role, **extra):
    return _post(client, event, "public_shift_withdraw", shift, {"role_id": role.pk, **extra})


def _summary_count(event, user):
    with scope(event=event):
        return TeamShiftsEmailQueue.objects.filter(is_shift_summary=True, recipients__user=user).count()


@pytest.mark.django_db
def test_claim_and_drop_share_one_daily_summary(client, event, application, volunteer, shift, role):
    client.force_login(volunteer)
    assert _claim(client, event, shift, role).status_code == 200
    assert _drop(client, event, shift, role).status_code == 200
    assert _claim(client, event, shift, role).status_code == 200
    assert _summary_count(event, volunteer) == 1


@pytest.mark.django_db
def test_summary_is_queued_even_when_volunteer_opted_out_of_action_emails(client, event, application, volunteer, shift, role):
    client.force_login(volunteer)
    with patch("teamshifts.views.queue_shift_notification_email") as mock_queue:
        _claim(client, event, shift, role, send_email=False)
    mock_queue.assert_not_called()
    assert _summary_count(event, volunteer) == 1


@pytest.mark.django_db
def test_organizer_assign_and_remove_queue_summary(client, event, application, volunteer, organizer_user, shift, role):
    client.force_login(organizer_user)
    base = reverse("plugins:teamshifts:api_assignments", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    with TestCase.captureOnCommitCallbacks(execute=True):
        resp = client.post(base, data=json.dumps({"shift_id": shift.pk, "user_id": volunteer.pk, "role_id": role.pk}), content_type="application/json")
    assert resp.status_code == 200
    assert _summary_count(event, volunteer) == 1
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(is_shift_summary=True).update(sent_at=now())
    with TestCase.captureOnCommitCallbacks(execute=True):
        resp = client.delete(f"{base}?shift_id={shift.pk}&user_id={volunteer.pk}&role_id={role.pk}")
    assert resp.status_code == 200
    assert _summary_count(event, volunteer) == 2

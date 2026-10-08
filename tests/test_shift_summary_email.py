import datetime
from unittest.mock import patch

import pytest
from django.utils.timezone import now
from django_scopes import scope

from teamshifts.models import (
    CallForTeamMembers,
    EmailTemplateRoles,
    Shift,
    ShiftAssignment,
    TeamRole,
    TeamShiftsEmailQueue,
)
from teamshifts.services.email import SHIFT_SUMMARY_DELAY, queue_shift_summary_email
from teamshifts.services.shift_summary import build_shift_summary, build_shift_summary_sample


@pytest.fixture
def volunteer(django_user_model):
    return django_user_model.objects.create_user(email="vol@example.com", password="x", fullname="Vol", locale="en")


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, shift_schedule_published=True)


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
def test_changes_are_debounced_into_one_summary(event, cfm, volunteer):
    shift = _shift(event, "Setup", now() + datetime.timedelta(days=3))
    with patch("teamshifts.services.email.send_queued_email") as task:
        queue_shift_summary_email(event, volunteer, shift=shift, signed_up=True)
        first = _summary_queues(event)[0].send_after
        queue_shift_summary_email(event, volunteer, shift=shift, signed_up=False)
        queues = _summary_queues(event)
    assert len(queues) == 1
    assert queues[0].send_after >= first
    assert queues[0].send_after > now() + SHIFT_SUMMARY_DELAY - datetime.timedelta(minutes=1)
    task.delay.assert_not_called()


@pytest.mark.django_db
def test_new_summary_after_previous_was_sent(event, cfm, volunteer):
    shift = _shift(event, "Setup", now() + datetime.timedelta(days=3))
    queue_shift_summary_email(event, volunteer, shift=shift, signed_up=True)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.update(sent_at=now())
    queue_shift_summary_email(event, volunteer, shift=shift, signed_up=True)
    assert len(_summary_queues(event)) == 2


@pytest.mark.django_db
def test_other_volunteers_are_not_merged(event, cfm, volunteer, django_user_model):
    other = django_user_model.objects.create_user(email="o@example.com", password="x", fullname="O", locale="en")
    shift = _shift(event, "Setup", now() + datetime.timedelta(days=3))
    queue_shift_summary_email(event, volunteer, shift=shift, signed_up=True)
    queue_shift_summary_email(event, other, shift=shift, signed_up=True)
    assert len(_summary_queues(event)) == 2


@pytest.mark.django_db
def test_signup_within_two_hours_sends_immediately(event, cfm, volunteer, django_capture_on_commit_callbacks):
    shift = _shift(event, "Soon", now() + datetime.timedelta(minutes=90))
    with patch("teamshifts.services.email.send_queued_email") as task, django_capture_on_commit_callbacks(execute=True):
        queue_shift_summary_email(event, volunteer, shift=shift, signed_up=True)
    assert _summary_queues(event)[0].send_after is None
    task.delay.assert_called_once()


@pytest.mark.django_db
def test_immediate_signup_flushes_pending_summary(event, cfm, volunteer, django_capture_on_commit_callbacks):
    later = _shift(event, "Later", now() + datetime.timedelta(days=3))
    soon = _shift(event, "Soon", now() + datetime.timedelta(minutes=30))
    queue_shift_summary_email(event, volunteer, shift=later, signed_up=True)
    with patch("teamshifts.services.email.send_queued_email") as task, django_capture_on_commit_callbacks(execute=True):
        queue_shift_summary_email(event, volunteer, shift=soon, signed_up=True)
    queues = _summary_queues(event)
    assert len(queues) == 1
    assert queues[0].send_after <= now()
    task.delay.assert_called_once()


@pytest.mark.django_db
def test_drop_of_imminent_shift_still_waits(event, cfm, volunteer):
    shift = _shift(event, "Soon", now() + datetime.timedelta(minutes=30))
    with patch("teamshifts.services.email.send_queued_email") as task:
        queue_shift_summary_email(event, volunteer, shift=shift, signed_up=False)
    assert _summary_queues(event)[0].send_after is not None
    task.delay.assert_not_called()


@pytest.mark.django_db
def test_skipped_when_schedule_unpublished(event, cfm, volunteer):
    with scope(event=event):
        cfm.shift_schedule_published = False
        cfm.save()
    shift = _shift(event, "Setup", now() + datetime.timedelta(days=3))
    assert queue_shift_summary_email(event, volunteer, shift=shift, signed_up=True) is None
    assert _summary_queues(event) == []


@pytest.mark.django_db
def test_summary_lists_all_current_shifts_sorted(event, cfm, volunteer, role):
    base = now() + datetime.timedelta(days=3)
    late = _shift(event, "Teardown", base + datetime.timedelta(days=1))
    early = _shift(event, "Setup", base)
    with scope(event=event):
        ShiftAssignment.objects.create(shift=late, team_member=volunteer, role=role)
        ShiftAssignment.objects.create(shift=early, team_member=volunteer, role=role)
    text = build_shift_summary(event, volunteer)
    assert text.index("Setup") < text.index("Teardown")
    assert "Role: Greeter" in text


@pytest.mark.django_db
def test_summary_without_shifts_links_to_schedule(event, cfm, volunteer):
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
def test_summary_sample_uses_the_events_own_shifts(event, cfm):
    _shift(event, "Registration Desk", now() + datetime.timedelta(days=3))
    assert "Registration Desk" in build_shift_summary_sample(event)


@pytest.mark.django_db
def test_summary_sample_without_shifts_falls_back(event, cfm):
    assert "no shifts" in build_shift_summary_sample(event)

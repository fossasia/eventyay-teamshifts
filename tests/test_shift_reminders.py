from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import User
from eventyay.base.services.mail import SendMailException

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftReminder,
    TeamMemberApplication,
    TeamRole,
)
from teamshifts.services.reminders import send_due_shift_reminders

MAIL_PATH = "teamshifts.services.reminders.mail"
SIGNED_UP_AT = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
FIRST_SHIFT_START = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
REMINDER_DUE_AT = FIRST_SHIFT_START - timedelta(hours=24)


@pytest.fixture
def volunteer(db):
    return User.objects.create_user(email="volunteer@example.com", password="secret", fullname="Jane Volunteer", locale="en")


@pytest.fixture
def role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Greeter")


@pytest.fixture
def location(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Main hall")


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, title="Join us", active=True, shift_schedule_published=True)


@pytest.fixture
def accepted(event, volunteer):
    with scope(event=event):
        return TeamMemberApplication.objects.create(event=event, user=volunteer, status=ApplicationStatus.ACCEPTED)


@pytest.fixture
def assign(event, volunteer, role, location):
    def _assign(start, *, hours=2, name="Shift", signed_up_at=SIGNED_UP_AT, user=None):
        with scope(event=event):
            shift = Shift.objects.create(
                event=event,
                name=name,
                location=location,
                start_time=start,
                end_time=start + timedelta(hours=hours),
            )
            assignment = ShiftAssignment.objects.create(shift=shift, team_member=user or volunteer, role=role)
            ShiftAssignment.objects.filter(pk=assignment.pk).update(assigned_at=signed_up_at)
        return assignment

    return _assign


@pytest.mark.django_db
def test_one_email_lists_all_shifts_of_the_day_sorted_by_time(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START + timedelta(hours=9), name="Evening")
    assign(FIRST_SHIFT_START, name="Morning")
    assign(FIRST_SHIFT_START + timedelta(hours=5), name="Noon")

    with patch(MAIL_PATH) as mock_mail:
        sent = send_due_shift_reminders(REMINDER_DUE_AT)

    assert sent == 1
    assert mock_mail.call_count == 1
    kwargs = mock_mail.call_args.kwargs
    lines = kwargs["context"]["shift_list"].split("\n")
    assert len(lines) == 3
    assert "Morning" in lines[0]
    assert "Noon" in lines[1]
    assert "Evening" in lines[2]
    assert all("Greeter" in line and "Main hall" in line for line in lines)
    assert "Oct" in lines[0]
    assert str(kwargs["subject"]) == "Reminder: your shifts tomorrow at {event_name}"
    assert kwargs["email"] == "volunteer@example.com"


@pytest.mark.django_db
def test_reminder_is_not_sent_twice(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)
        send_due_shift_reminders(REMINDER_DUE_AT + timedelta(hours=1))

    assert mock_mail.call_count == 1


@pytest.mark.django_db
def test_not_sent_before_24_hours_ahead(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT - timedelta(minutes=5))

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_shift_signed_up_less_than_24_hours_ahead_gets_no_reminder(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START, signed_up_at=FIRST_SHIFT_START - timedelta(hours=10))

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT + timedelta(hours=1))

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_signup_after_reminder_was_sent_gets_no_second_reminder(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)
        assign(FIRST_SHIFT_START + timedelta(hours=9), name="Evening", signed_up_at=REMINDER_DUE_AT + timedelta(minutes=30))
        send_due_shift_reminders(REMINDER_DUE_AT + timedelta(hours=9))

    assert mock_mail.call_count == 1


@pytest.mark.django_db
def test_failed_send_is_retried_on_next_run(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)

    with patch(MAIL_PATH, side_effect=SendMailException("boom")):
        assert send_due_shift_reminders(REMINDER_DUE_AT) == 0
    with scope(event=event):
        assert not ShiftReminder.objects.filter(event=event).exists()

    with patch(MAIL_PATH) as mock_mail:
        assert send_due_shift_reminders(REMINDER_DUE_AT + timedelta(minutes=15)) == 1
    assert mock_mail.call_count == 1


@pytest.mark.django_db
def test_my_shifts_page_offers_drop_action(client, event, cfm, accepted, assign, volunteer):
    assign(datetime.now(UTC) + timedelta(days=3))
    client.force_login(volunteer)

    response = client.get(
        reverse("plugins:teamshifts:my_shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug}),
    )

    assert response.status_code == 200
    assert "Drop shift" in response.content.decode()

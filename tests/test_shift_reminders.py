from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import User
from i18nfield.strings import LazyI18nString

from teamshifts.forms import EmailTemplateForm
from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftReminder,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
)
from teamshifts.services.reminders import MAX_ATTEMPTS, send_due_shift_reminders

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
    kwargs = mock_mail.call_args.kwargs
    lines = kwargs["context"]["shift_list"].split("\n")
    assert len(lines) == 3
    assert "Morning" in lines[0]
    assert "Noon" in lines[1]
    assert "Evening" in lines[2]
    assert all("Greeter" in line and "Main hall" in line for line in lines)
    assert str(kwargs["subject"]) == "Reminder: your shifts tomorrow at {event_name}"
    assert "{my_shifts_url}" in str(kwargs["template"])
    assert kwargs["context"]["my_shifts_url"].endswith("/common/my-shifts/")
    assert kwargs["email"] == "volunteer@example.com"
    with scope(event=event):
        sent_record = TeamShiftsEmailQueue.objects.get(event=event, mail_type=EmailTemplateRoles.SHIFT_REMINDER)
    assert sent_record.sent_at is not None
    assert sent_record.mail_type_label == "Shift reminder"


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
def test_list_includes_late_signups_once_the_reminder_is_due(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START, name="Late signup", signed_up_at=FIRST_SHIFT_START - timedelta(hours=23))
    planned = assign(FIRST_SHIFT_START + timedelta(hours=5), name="Planned")

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(planned.shift.start_time - timedelta(hours=24))

    lines = mock_mail.call_args.kwargs["context"]["shift_list"].split("\n")
    assert len(lines) == 2
    assert "Late signup" in lines[0]
    assert "Planned" in lines[1]


@pytest.mark.django_db
def test_signup_after_reminder_was_sent_gets_no_second_reminder(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)
        assign(FIRST_SHIFT_START + timedelta(hours=9), name="Evening", signed_up_at=REMINDER_DUE_AT + timedelta(minutes=30))
        send_due_shift_reminders(REMINDER_DUE_AT + timedelta(hours=9))

    assert mock_mail.call_count == 1


@pytest.mark.django_db
def test_no_reminder_once_first_shift_of_the_day_has_started(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)
    assign(FIRST_SHIFT_START + timedelta(hours=5))

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(FIRST_SHIFT_START + timedelta(hours=1))

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_no_reminder_while_shift_schedule_is_unpublished(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)
    cfm.shift_schedule_published = False
    cfm.save(update_fields=["shift_schedule_published"])

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_automatic_sending_can_be_turned_off_on_the_template(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)
    template = cfm.get_mail_template(EmailTemplateRoles.SHIFT_REMINDER)
    assert template.send_automatically
    assert "send_automatically" in EmailTemplateForm(instance=template).fields
    assert "send_automatically" not in EmailTemplateForm(instance=cfm.get_mail_template(EmailTemplateRoles.APPLICATION_ACCEPTED)).fields

    with scope(event=event):
        template.send_automatically = False
        template.save(update_fields=["send_automatically"])

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_html_template_gets_one_line_per_shift(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START, name="Morning")
    assign(FIRST_SHIFT_START + timedelta(hours=5), name="<b>Noon</b>")
    template = cfm.get_mail_template(EmailTemplateRoles.SHIFT_REMINDER)
    with scope(event=event):
        template.body = LazyI18nString({"en": "<p>{shift_list}</p>"})
        template.save(update_fields=["body"])

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)

    shift_list = mock_mail.call_args.kwargs["context"]["shift_list"]
    assert shift_list.count("<br>") == 1
    assert "&lt;b&gt;Noon&lt;/b&gt;" in shift_list


@pytest.mark.django_db
def test_failed_send_is_retried_after_interval(event, cfm, accepted, assign):
    assign(FIRST_SHIFT_START)

    with patch(MAIL_PATH, side_effect=ValueError("bad template")):
        assert send_due_shift_reminders(REMINDER_DUE_AT) == 0
    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT + timedelta(minutes=15))
    mock_mail.assert_not_called()

    with scope(event=event):
        ShiftReminder.objects.filter(event=event).update(claimed_at=now() - timedelta(hours=1))
    with patch(MAIL_PATH) as mock_mail:
        assert send_due_shift_reminders(REMINDER_DUE_AT + timedelta(hours=1)) == 1

    with scope(event=event):
        reminder = ShiftReminder.objects.get(event=event)
    assert reminder.attempts == 2
    assert reminder.sent_at is not None


@pytest.mark.django_db
def test_retries_stop_after_max_attempts(event, cfm, accepted, assign, volunteer):
    assign(FIRST_SHIFT_START)
    with scope(event=event):
        reminder = ShiftReminder.objects.create(event=event, user=volunteer, day=date(2026, 10, 11), attempts=MAX_ATTEMPTS)
        ShiftReminder.objects.filter(pk=reminder.pk).update(claimed_at=now() - timedelta(hours=1))

    with patch(MAIL_PATH) as mock_mail:
        send_due_shift_reminders(REMINDER_DUE_AT)

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_my_shifts_offers_drop_only_for_upcoming_shifts_on_a_published_schedule(client, event, cfm, accepted, assign, volunteer):
    assign(now() + timedelta(days=3), name="Upcoming")
    assign(now() - timedelta(hours=1), name="In progress")
    client.force_login(volunteer)
    url = reverse("plugins:teamshifts:my_shifts_global")

    assert client.get(url).content.decode().count("Drop shift") == 1

    cfm.shift_schedule_published = False
    cfm.save(update_fields=["shift_schedule_published"])
    assert "Drop shift" not in client.get(url).content.decode()


@pytest.mark.django_db
def test_volunteer_cannot_drop_a_started_shift(client, event, cfm, accepted, assign, volunteer):
    started = assign(now() - timedelta(minutes=5), name="Started")
    upcoming = assign(now() + timedelta(days=2), name="Upcoming")
    client.force_login(volunteer)
    headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def withdraw(assignment):
        url = reverse("plugins:teamshifts:public_shift_withdraw", kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": assignment.shift_id})
        return client.post(url, {"role_id": assignment.role_id}, **headers)

    response = withdraw(started)
    assert response.status_code == 400
    assert "already started" in response.json()["error"]
    assert withdraw(upcoming).status_code == 200
    with scope(event=event):
        assert ShiftAssignment.objects.filter(pk=started.pk).exists()
        assert not ShiftAssignment.objects.filter(pk=upcoming.pk).exists()

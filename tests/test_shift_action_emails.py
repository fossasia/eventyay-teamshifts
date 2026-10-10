import json
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now, timedelta
from django_scopes import scope
from eventyay.base.models import Event, Team, User

from teamshifts.mail.default_templates import get_default_template
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

JSON_HEADERS = {"HTTP_ACCEPT": "application/json", "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def site_url(settings):
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True, shift_schedule_published=True)


@pytest.fixture
def volunteer(django_user_model):
    return django_user_model.objects.create_user(email="volunteer@example.com", password="x")


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
def team_role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Registration")


def _make_shift(event, role, hours_from_now):
    with scope(event=event):
        location, _created = ShiftLocation.objects.get_or_create(event=event, name="Hall A")
        shift = Shift.objects.create(
            event=event,
            name=f"Shift +{hours_from_now}h",
            location=location,
            start_time=now() + timedelta(hours=hours_from_now),
            end_time=now() + timedelta(hours=hours_from_now + 2),
        )
        ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=3)
    return shift


@pytest.fixture
def shift(event, team_role):
    return _make_shift(event, team_role, 24)


def _post(client, event, name, shift, payload):
    url = reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk})
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(url, data=json.dumps(payload), content_type="application/json", **JSON_HEADERS)


def _claim(client, event, shift, role, **extra):
    return _post(client, event, "public_shift_claim", shift, {"role_id": role.pk, **extra})


def _drop(client, event, shift, role, **extra):
    return _post(client, event, "public_shift_withdraw", shift, {"role_id": role.pk, **extra})


def _sent_roles(mock_queue):
    return [c.kwargs["template_role"] for c in mock_queue.call_args_list]


def _stored_choice(event, application):
    with scope(event=event):
        application.refresh_from_db()
    return application.shift_action_emails


@pytest.mark.django_db
@patch("teamshifts.views.queue_shift_notification_email")
def test_claim_emails_by_default(mock_queue, client, event, application, volunteer, shift, team_role):
    client.force_login(volunteer)

    response = _claim(client, event, shift, team_role)

    assert response.status_code == 200
    assert response.json()["shift_action_emails"] is True
    assert _sent_roles(mock_queue) == [EmailTemplateRoles.SHIFT_CLAIMED_BY_VOLUNTEER]
    assert _stored_choice(event, application) is True


@pytest.mark.django_db
@patch("teamshifts.views.queue_shift_notification_email")
def test_claim_opt_out_skips_email_and_is_remembered(mock_queue, client, event, application, volunteer, shift, team_role):
    client.force_login(volunteer)
    later_shift = _make_shift(event, team_role, 48)

    response = _claim(client, event, shift, team_role, send_email=False)

    assert response.status_code == 200
    assert response.json()["shift_action_emails"] is False
    assert mock_queue.call_count == 0
    assert _stored_choice(event, application) is False
    with scope(event=event):
        assert ShiftAssignment.objects.filter(shift=shift, team_member=volunteer).exists()

    # A request without a choice falls back to the saved one.
    response = _claim(client, event, later_shift, team_role)
    assert response.status_code == 200
    assert mock_queue.call_count == 0

    # Ticking the box again turns emails back on.
    third_shift = _make_shift(event, team_role, 72)
    _claim(client, event, third_shift, team_role, send_email=True)
    assert _sent_roles(mock_queue) == [EmailTemplateRoles.SHIFT_CLAIMED_BY_VOLUNTEER]
    assert _stored_choice(event, application) is True


@pytest.mark.django_db
@patch("teamshifts.views._notify_organizers_shift_dropped")
@patch("teamshifts.views.queue_shift_notification_email")
def test_drop_emails_by_default(mock_queue, _mock_orga, client, event, application, volunteer, shift, team_role):
    with scope(event=event):
        ShiftAssignment.objects.create(shift=shift, team_member=volunteer, role=team_role)
    client.force_login(volunteer)

    response = _drop(client, event, shift, team_role)

    assert response.status_code == 200
    assert response.json()["shift_action_emails"] is True
    assert _sent_roles(mock_queue) == [EmailTemplateRoles.SHIFT_DROPPED_BY_VOLUNTEER]
    assert mock_queue.call_args.kwargs["role"] == team_role


@pytest.mark.django_db
@patch("teamshifts.services.email.send_queued_email")
@patch("teamshifts.views.queue_shift_notification_email")
def test_drop_opt_out_skips_email(mock_queue, _mock_send, client, event, application, volunteer, organizer_user, shift, team_role):
    with scope(event=event):
        ShiftAssignment.objects.create(shift=shift, team_member=volunteer, role=team_role)
    client.force_login(volunteer)

    response = _drop(client, event, shift, team_role, send_email=False)

    assert response.status_code == 200
    assert response.json()["shift_action_emails"] is False
    assert mock_queue.call_count == 0
    assert _stored_choice(event, application) is False
    with scope(event=event):
        assert not ShiftAssignment.objects.filter(shift=shift, team_member=volunteer).exists()
        # Organizers are still told about the drop.
        queue = TeamShiftsEmailQueue.objects.get(event=event)
        assert [r.email for r in queue.recipients.all()] == [organizer_user.email]
        assert str(queue.subject) == str(get_default_template(EmailTemplateRoles.SHIFT_DROPPED_ORGANIZER)[0])
        assert (queue.user, queue.shift, queue.shift_role) == (volunteer, shift, team_role)


@pytest.mark.django_db
@patch("teamshifts.services.email.send_queued_email")
@patch("teamshifts.views.queue_shift_notification_email")
def test_concurrent_drop_sends_no_emails(mock_queue, _mock_send, client, event, application, volunteer, organizer_user, shift, team_role):
    with scope(event=event):
        ShiftAssignment.objects.create(shift=shift, team_member=volunteer, role=team_role)
    client.force_login(volunteer)

    # Another request deleted the assignment between this request's lookup and its delete.
    with patch("django.db.models.query.QuerySet.delete", return_value=(0, {})):
        response = _drop(client, event, shift, team_role, send_email=False)

    assert response.status_code == 400
    assert mock_queue.call_count == 0
    assert _stored_choice(event, application) is True
    with scope(event=event):
        assert not TeamShiftsEmailQueue.objects.filter(event=event).exists()


@pytest.mark.django_db
@patch("teamshifts.views.queue_shift_notification_email")
def test_failed_claim_does_not_change_saved_choice(mock_queue, client, event, application, volunteer, shift):
    with scope(event=event):
        other_role = TeamRole.objects.create(event=event, name="Not on this shift")
    client.force_login(volunteer)

    response = _claim(client, event, shift, other_role, send_email=False)

    assert response.status_code == 400
    assert _stored_choice(event, application) is True


@pytest.mark.django_db
def test_schedule_prefills_saved_choice(client, event, application, volunteer):
    with scope(event=event):
        TeamMemberApplication.objects.filter(pk=application.pk).update(shift_action_emails=False)
    client.force_login(volunteer)
    kwargs = {"organizer": event.organizer.slug, "event": event.slug}

    response = client.get(reverse("plugins:teamshifts:public_shift_schedule_api", kwargs=kwargs))
    assert response.json()["shift_action_emails"] is False

    response = client.get(reverse("plugins:teamshifts:public_shift_schedule", kwargs=kwargs))
    assert '"shift_action_emails": false' in response.context["schedule_data_json"]


@pytest.mark.django_db
@patch("teamshifts.views.queue_shift_notification_email")
def test_choice_is_stored_per_event(mock_queue, client, event, application, volunteer, shift, team_role):
    other_event = Event.objects.create(
        organizer=event.organizer,
        name="Other Event",
        slug="other-event",
        live=True,
        date_from=now(),
        plugins="teamshifts",
    )
    with scope(event=other_event):
        CallForTeamMembers.objects.create(event=other_event, active=True, shift_schedule_published=True)
        other_application = TeamMemberApplication.objects.create(event=other_event, user=volunteer, status=ApplicationStatus.ACCEPTED)
    client.force_login(volunteer)

    _claim(client, event, shift, team_role, send_email=False)

    assert _stored_choice(event, application) is False
    assert _stored_choice(other_event, other_application) is True


@pytest.mark.django_db
@patch("teamshifts.views.queue_shift_notification_email")
def test_organizer_assignment_still_emails_after_opt_out(mock_queue, client, event, application, volunteer, organizer_user, shift, team_role):
    with scope(event=event):
        TeamMemberApplication.objects.filter(pk=application.pk).update(shift_action_emails=False)
    client.force_login(organizer_user)
    url = reverse("plugins:teamshifts:api_assignments", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = client.post(
            url,
            data=json.dumps({"shift_id": shift.pk, "user_id": volunteer.pk, "role_id": team_role.pk}),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert _sent_roles(mock_queue) == [EmailTemplateRoles.SHIFT_ASSIGNED_BY_ORGANIZER]


def test_drop_confirmation_has_default_template():
    subject, body = get_default_template(EmailTemplateRoles.SHIFT_DROPPED_BY_VOLUNTEER)
    assert "{event_name}" in str(subject)
    assert "{shift_name}" in str(body)
    assert "{shift_schedule_url}" in str(body)


@pytest.mark.django_db
def test_new_applications_default_to_emails_on(event, cfm):
    user = User.objects.create_user(email="new@example.com", password="x")
    with scope(event=event):
        app = TeamMemberApplication.objects.create(event=event, user=user)
    assert app.shift_action_emails is True

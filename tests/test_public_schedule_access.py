from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now, timedelta
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, Team, User

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
)


@pytest.fixture
def event(db):
    organizer = Organizer.objects.create(name="FOSSASIA", slug="fossasia")
    return Event.objects.create(
        name="FOSSASIA Summit",
        slug="fossasia-summit",
        organizer=organizer,
        date_from=now(),
        date_to=now() + timedelta(days=2),
        plugins="teamshifts",
    )


@pytest.fixture
def shift(event):
    with scopes_disabled():
        CallForTeamMembers.objects.create(event=event, title="Join", active=True, shift_schedule_published=True)
        location = ShiftLocation.objects.create(event=event, name="Main Hall")
        return Shift.objects.create(event=event, name="Morning Shift", location=location, start_time=now(), end_time=now() + timedelta(hours=3))


def _login_with_team(client, event, **team_kwargs):
    user = User.objects.create_user(email="user@example.com", password="password")
    if team_kwargs:
        with scopes_disabled():
            team = Team.objects.create(organizer=event.organizer, name="Team", all_events=True, **team_kwargs)
            team.members.add(user)
    client.force_login(user)


def _url(name, event, **extra):
    return reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, **extra})


@pytest.mark.django_db
@pytest.mark.parametrize("team_kwargs", [{"teamshifts_role": "coordinator"}, {"teamshifts_role": "lead"}, {"can_change_event_settings": True}])
def test_teamshifts_managers_can_view_schedule_without_applying(client, event, shift, team_kwargs):
    _login_with_team(client, event, **team_kwargs)

    page = client.get(_url("public_shift_schedule", event))
    assert page.status_code == 200
    assert b"Morning Shift" in page.content
    assert client.get(_url("public_shift_schedule_api", event)).status_code == 200


@pytest.mark.django_db
def test_unpublished_schedule_shows_notice_to_managers(client, event, shift):
    with scopes_disabled():
        CallForTeamMembers.objects.filter(event=event).update(shift_schedule_published=False)
    _login_with_team(client, event, teamshifts_role="coordinator")

    response = client.get(_url("public_shift_schedule", event))
    assert response.status_code == 200
    assert response.context["shift_schedule_published"] is False
    assert b"Morning Shift" not in response.content


@pytest.mark.django_db
def test_user_without_teamshifts_access_is_redirected_to_apply(client, event, shift):
    _login_with_team(client, event, can_view_orders=True)

    response = client.get(_url("public_shift_schedule", event))
    assert response.status_code == 302
    assert response.url == _url("apply", event)


@pytest.mark.django_db
def test_managers_can_claim_and_withdraw_shifts_without_applying(client, event, shift):
    _login_with_team(client, event, teamshifts_role="lead")
    with scopes_disabled():
        role = TeamRole.objects.create(event=event, name="Registration")
        ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=2)
    headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    claim = client.post(_url("public_shift_claim", event, pk=shift.pk), {"role_id": role.pk}, **headers)
    assert claim.status_code == 200
    with scopes_disabled():
        application = TeamMemberApplication.objects.get(event=event, user__email="user@example.com")
    assert application.status == ApplicationStatus.ACCEPTED
    assert client.get(_url("my_shifts", event)).status_code == 200

    withdraw = client.post(_url("public_shift_withdraw", event, pk=shift.pk), {"role_id": role.pk}, **headers)
    assert withdraw.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("previous_status", "emails"),
    [(None, 0), (ApplicationStatus.PENDING, 1), (ApplicationStatus.REJECTED, 1)],
)
@patch("teamshifts.views.queue_lifecycle_email")
def test_manager_claim_emails_only_when_existing_application_is_accepted(
    mock_queue, django_capture_on_commit_callbacks, client, event, shift, previous_status, emails
):
    _login_with_team(client, event, teamshifts_role="lead")
    with scopes_disabled():
        role = TeamRole.objects.create(event=event, name="Registration")
        ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=2)
        if previous_status:
            TeamMemberApplication.objects.create(event=event, user=User.objects.get(email="user@example.com"), status=previous_status)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_url("public_shift_claim", event, pk=shift.pk), {"role_id": role.pk}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 200
    assert mock_queue.call_count == emails
    if emails:
        assert mock_queue.call_args.args[1] == EmailTemplateRoles.APPLICATION_ACCEPTED


@pytest.mark.django_db
@patch("teamshifts.views.queue_lifecycle_email", side_effect=RuntimeError("mail template missing"))
def test_manager_claim_succeeds_when_acceptance_email_fails(mock_queue, django_capture_on_commit_callbacks, client, event, shift):
    _login_with_team(client, event, teamshifts_role="lead")
    with scopes_disabled():
        role = TeamRole.objects.create(event=event, name="Registration")
        ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=2)
        TeamMemberApplication.objects.create(event=event, user=User.objects.get(email="user@example.com"), status=ApplicationStatus.PENDING)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_url("public_shift_claim", event, pk=shift.pk), {"role_id": role.pk}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 200
    mock_queue.assert_called_once()
    with scopes_disabled():
        assert ShiftAssignment.objects.filter(shift=shift, team_member__email="user@example.com").exists()

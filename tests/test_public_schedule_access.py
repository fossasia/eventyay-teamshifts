import pytest
from django.urls import reverse
from django.utils.timezone import now, timedelta
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, Team, User

from teamshifts.models import CallForTeamMembers, Shift, ShiftLocation, ShiftRoleAssignment, TeamRole


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
    assert client.get(_url("my_shifts", event)).status_code == 200

    withdraw = client.post(_url("public_shift_withdraw", event, pk=shift.pk), {"role_id": role.pk}, **headers)
    assert withdraw.status_code == 200

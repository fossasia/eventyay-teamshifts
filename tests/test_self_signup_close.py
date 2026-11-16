import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, Team, User

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
)

CLOSED_MESSAGE = "Sign-up for shifts has closed. Please contact the organizers if you'd like to help."
STARTED_MESSAGE = "This shift has already started."
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def event(db, settings):
    settings.SITE_URL = "https://testserver"
    organizer = Organizer.objects.create(name="FOSSASIA", slug="fossasia")
    event = Event.objects.create(
        name="FOSSASIA Summit",
        slug="fossasia-summit",
        organizer=organizer,
        date_from=now(),
        date_to=now() + timedelta(days=2),
        plugins="teamshifts",
        timezone="Asia/Singapore",
    )
    event.settings.timezone = "Asia/Singapore"
    with scopes_disabled():
        CallForTeamMembers.objects.create(event=event, title="Join", active=True, shift_schedule_published=True)
    return event


@pytest.fixture
def role(event):
    with scopes_disabled():
        return TeamRole.objects.create(event=event, name="Registration")


def _make_shift(event, role, start):
    with scopes_disabled():
        location, _created = ShiftLocation.objects.get_or_create(event=event, name="Main Hall")
        shift = Shift.objects.create(event=event, name="Shift", location=location, start_time=start, end_time=start + timedelta(hours=2))
        ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=2)
        return shift


@pytest.fixture
def shift(event, role):
    return _make_shift(event, role, now() + timedelta(days=1))


@pytest.fixture
def started_shift(event, role):
    return _make_shift(event, role, now() - timedelta(minutes=5))


@pytest.fixture
def member(event):
    user = User.objects.create_user(email="member@example.com", password="password")
    with scopes_disabled():
        TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED)
    client = Client()
    client.force_login(user)
    client.user = user
    return client


def _team_client(event, email, **team_kwargs):
    user = User.objects.create_user(email=email, password="password")
    with scopes_disabled():
        team = Team.objects.create(organizer=event.organizer, name=email, all_events=True, **team_kwargs)
        team.members.add(user)
    client = Client()
    client.force_login(user)
    client.user = user
    return client


@pytest.fixture
def organizer(event):
    return _team_client(event, "orga@example.com", can_change_event_settings=True)


@pytest.fixture
def lead(event):
    return _team_client(event, "lead@example.com", teamshifts_role="lead")


def _url(name, event, **extra):
    return reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, **extra})


def _close_signup(event, closes_at):
    with scopes_disabled():
        CallForTeamMembers.objects.filter(event=event).update(self_signup_closes_at=closes_at)


def _closes_at(event):
    with scopes_disabled():
        return CallForTeamMembers.objects.get(event=event).self_signup_closes_at


def _schedule_data(response):
    return json.loads(response.context["schedule_data_json"])


@pytest.mark.django_db
def test_organizer_sets_close_time_in_event_timezone(event, organizer):
    response = organizer.post(_url("schedule_self_signup", event), {"action": "save", "self_signup_closes_at": "2030-05-01T18:30"})

    assert response.status_code == 302
    assert _closes_at(event) == datetime(2030, 5, 1, 18, 30, tzinfo=ZoneInfo("Asia/Singapore"))


@pytest.mark.django_db
@pytest.mark.parametrize("data", [{"action": "clear", "self_signup_closes_at": "2030-05-01T18:30"}, {"action": "save", "self_signup_closes_at": ""}])
def test_clearing_or_saving_empty_keeps_signup_open(event, organizer, data):
    _close_signup(event, now() - timedelta(hours=1))

    organizer.post(_url("schedule_self_signup", event), data)

    assert _closes_at(event) is None


@pytest.mark.django_db
def test_invalid_close_time_is_rejected(event, organizer):
    organizer.post(_url("schedule_self_signup", event), {"action": "save", "self_signup_closes_at": "not a date"})

    assert _closes_at(event) is None


@pytest.mark.django_db
def test_team_member_cannot_change_close_time(event, member):
    response = member.post(_url("schedule_self_signup", event), {"action": "save", "self_signup_closes_at": "2030-05-01T18:30"})

    assert response.status_code in (302, 403, 404)
    assert _closes_at(event) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("closes_in", "label"),
    [(None, "Sign-up: open"), (timedelta(days=1), "Sign-up: open until"), (-timedelta(minutes=1), "Sign-up: closed")],
)
def test_grid_shows_signup_state(event, organizer, settings, closes_in, label):
    settings.VITE_DEV_MODE = True
    _close_signup(event, now() + closes_in if closes_in else None)

    response = organizer.get(_url("schedule_grid", event))

    assert response.status_code == 200
    assert label in response.content.decode()


@pytest.mark.django_db
def test_member_can_sign_up_before_close_time_and_sees_notice(event, member, shift, role):
    _close_signup(event, now() + timedelta(days=1))

    page = member.get(_url("public_shift_schedule", event))
    assert "Sign-up for shifts closes on" in page.content.decode()
    assert _schedule_data(page)["can_self_signup"] is True

    response = member.post(_url("public_shift_claim", event, pk=shift.pk), {"role_id": role.pk}, **AJAX)
    assert response.status_code == 200


@pytest.mark.django_db
def test_no_notice_when_no_close_time_is_set(event, member, shift):
    page = member.get(_url("public_shift_schedule", event))

    assert "Sign-up for shifts" not in page.content.decode()
    assert _schedule_data(page)["can_self_signup"] is True


@pytest.mark.django_db
def test_member_cannot_sign_up_after_close_time(event, member, shift, role):
    _close_signup(event, now() - timedelta(minutes=1))

    page = member.get(_url("public_shift_schedule", event))
    assert page.status_code == 200
    assert CLOSED_MESSAGE in page.content.decode().replace("&#x27;", "'")
    assert _schedule_data(page)["can_self_signup"] is False
    assert member.get(_url("public_shift_schedule_api", event)).json()["can_self_signup"] is False

    response = member.post(_url("public_shift_claim", event, pk=shift.pk), {"role_id": role.pk}, **AJAX)
    assert response.status_code == 400
    assert response.json()["error"] == CLOSED_MESSAGE
    with scopes_disabled():
        assert not ShiftAssignment.objects.filter(shift=shift).exists()


@pytest.mark.django_db
def test_member_can_still_drop_upcoming_shift_after_close_time(event, member, shift, role):
    with scopes_disabled():
        ShiftAssignment.objects.create(shift=shift, team_member=member.user, role=role)
    _close_signup(event, now() - timedelta(minutes=1))

    response = member.post(_url("public_shift_withdraw", event, pk=shift.pk), {"role_id": role.pk}, **AJAX)

    assert response.status_code == 200
    with scopes_disabled():
        assert not ShiftAssignment.objects.filter(shift=shift).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("client_fixture", ["organizer", "lead"])
def test_organizers_can_still_sign_themselves_up_after_close_time(request, event, shift, role, client_fixture):
    client = request.getfixturevalue(client_fixture)
    _close_signup(event, now() - timedelta(minutes=1))

    page = client.get(_url("public_shift_schedule", event))
    assert "Sign-up for shifts" not in page.content.decode()
    assert _schedule_data(page)["can_self_signup"] is True

    response = client.post(_url("public_shift_claim", event, pk=shift.pk), {"role_id": role.pk}, **AJAX)
    assert response.status_code == 200


@pytest.mark.django_db
def test_organizers_can_still_assign_members_after_close_time(event, organizer, member, shift, role):
    _close_signup(event, now() - timedelta(minutes=1))

    response = organizer.post(
        _url("public_shift_assignments", event),
        data=json.dumps({"shift_id": shift.pk, "user_id": member.user.pk, "role_id": role.pk}),
        content_type="application/json",
    )

    assert response.status_code == 200
    with scopes_disabled():
        assert ShiftAssignment.objects.filter(shift=shift, team_member=member.user).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("client_fixture", ["member", "organizer"])
def test_nobody_can_sign_up_for_a_started_shift(request, event, started_shift, role, client_fixture):
    client = request.getfixturevalue(client_fixture)

    response = client.post(_url("public_shift_claim", event, pk=started_shift.pk), {"role_id": role.pk}, **AJAX)

    assert response.status_code == 400
    assert response.json()["error"] == STARTED_MESSAGE
    with scopes_disabled():
        assert not ShiftAssignment.objects.filter(shift=started_shift).exists()


@pytest.mark.django_db
def test_close_time_is_entered_and_shown_in_event_timezone(event, organizer, member, shift):
    organizer.post(_url("schedule_self_signup", event), {"action": "save", "self_signup_closes_at": "2099-05-01T18:30"})

    assert _closes_at(event) == datetime(2099, 5, 1, 10, 30, tzinfo=ZoneInfo("UTC"))
    assert "Sign-up for shifts closes on 2099-05-01 18:30." in member.get(_url("public_shift_schedule", event)).content.decode()

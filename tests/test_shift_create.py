from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team

from teamshifts.models import Shift, ShiftLocation, TeamRole


@pytest.fixture
def orga_client(client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    client.force_login(user)
    return client


@pytest.fixture
def location(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Main Hall")


@pytest.fixture
def team_role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Volunteer")


@pytest.mark.django_db
def test_shift_create_single_success(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=2)

    data = {
        "mode": "single",
        "name": "Test Shift",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": "",
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "2",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 302

    with scope(event=event):
        assert Shift.objects.count() == 1
        shift = Shift.objects.first()
        assert shift.name == "Test Shift"
        assert shift.location == location
        assert shift.assignments.count() == 0
        assert shift.role_assignments.count() == 1


@pytest.mark.django_db
def test_shift_create_repeating_success(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=4)  # 4 hours total

    data = {
        "mode": "repeating",
        "name": "Rep Shift",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": "120",  # 2 hours per shift -> 2 shifts
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 200
    assert b"2 shifts created successfully" in response.content

    with scope(event=event):
        assert Shift.objects.count() == 2


@pytest.mark.django_db
def test_shift_create_repeating_invalid_remainder(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=4)  # 4 hours total

    data = {
        "mode": "repeating",
        "name": "Rep Shift",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": "90",  # 1.5 hours per shift doesn't divide 4 hours exactly
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 200
    assert b"The shift length must divide evenly into the total duration between start and end time" in response.content

    with scope(event=event):
        assert Shift.objects.count() == 0


@pytest.mark.django_db
def test_shift_create_missing_role(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=2)

    data = {
        "mode": "single",
        "name": "Test Shift",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": "",
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": "",
        "roles-0-capacity": "2",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 200
    assert b"At least one role must be added to the shift" in response.content

    with scope(event=event):
        assert Shift.objects.count() == 0

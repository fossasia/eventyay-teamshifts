import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    ShiftAssignment,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
)


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
def volunteer(db):
    return User.objects.create_user(email="volunteer@example.com", password="password")


@pytest.fixture
def member_client(volunteer, event, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        TeamMemberApplication.objects.create(event=event, user=volunteer, status=ApplicationStatus.ACCEPTED)
    plain_client = Client()
    plain_client.force_login(volunteer)
    return plain_client


@pytest.fixture
def team_role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Registration")


@pytest.fixture
def shift(event, team_role):
    with scope(event=event):
        shift = Shift.objects.create(
            event=event,
            name="Morning Shift",
            start_time=now(),
            end_time=now() + timedelta(hours=3),
        )
        ShiftRoleAssignment.objects.create(shift=shift, role=team_role, capacity=2)
        return shift


@pytest.mark.django_db
def test_organizer_can_edit_shift_via_public_manage_url(orga_client, event, shift, team_role):
    url = reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    payload = {
        "title": {"en": "Updated Shift Name"},
        "roles": [{"id": team_role.pk, "capacity": 3}],
    }
    response = orga_client.patch(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["talk"]["title"]["en"] == "Updated Shift Name"
    assert body["talk"]["roles"][0]["capacity"] == 3

    with scope(event=event):
        shift.refresh_from_db()
        assert shift.name == "Updated Shift Name"
        assert shift.role_assignments.get(role=team_role).capacity == 3


@pytest.mark.django_db
def test_organizer_can_delete_shift_via_public_manage_url(orga_client, event, shift):
    url = reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    response = orga_client.delete(url)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    with scope(event=event):
        assert not Shift.objects.filter(pk=shift.pk).exists()


@pytest.mark.django_db
def test_organizer_can_assign_and_unassign_member_via_public_assignments_url(orga_client, event, shift, team_role, volunteer, member_client):
    url = reverse(
        "plugins:teamshifts:public_shift_assignments",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    assign_payload = {"shift_id": shift.pk, "user_id": volunteer.pk, "role_id": team_role.pk}
    response = orga_client.post(url, data=json.dumps(assign_payload), content_type="application/json")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assigned_ids = {assignee["id"] for role in body["roles"] for assignee in role["assigned"]}
    assert volunteer.pk in assigned_ids

    with scope(event=event):
        assert ShiftAssignment.objects.filter(shift=shift, team_member=volunteer, role=team_role).exists()

    response = orga_client.delete(f"{url}?shift_id={shift.pk}&user_id={volunteer.pk}&role_id={team_role.pk}")

    assert response.status_code == 200
    body = response.json()
    assigned_ids = {assignee["id"] for role in body["roles"] for assignee in role["assigned"]}
    assert volunteer.pk not in assigned_ids

    with scope(event=event):
        assert not ShiftAssignment.objects.filter(shift=shift, team_member=volunteer).exists()


@pytest.mark.django_db
def test_volunteer_cannot_manage_shift_via_public_url(member_client, event, shift):
    url = reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    response = member_client.patch(url, data=json.dumps({"title": {"en": "Hacked"}}), content_type="application/json")

    assert response.status_code == 403
    with scope(event=event):
        shift.refresh_from_db()
        assert shift.name == "Morning Shift"


@pytest.mark.django_db
def test_volunteer_cannot_assign_via_public_assignments_url(member_client, event, shift, team_role, volunteer):
    url = reverse(
        "plugins:teamshifts:public_shift_assignments",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    payload = {"shift_id": shift.pk, "user_id": volunteer.pk, "role_id": team_role.pk}
    response = member_client.post(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 403
    with scope(event=event):
        assert not ShiftAssignment.objects.filter(shift=shift, team_member=volunteer).exists()


@pytest.mark.django_db
def test_can_manage_shifts_flag_in_public_schedule_api(orga_client, member_client, event, user, shift):
    with scope(event=event):
        CallForTeamMembers.objects.create(event=event, active=True, shift_schedule_published=True)
        TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED)

    url = reverse(
        "plugins:teamshifts:public_shift_schedule_api",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    organizer_response = orga_client.get(url)
    assert organizer_response.status_code == 200
    assert organizer_response.json()["can_manage_shifts"] is True

    volunteer_response = member_client.get(url)
    assert volunteer_response.status_code == 200
    assert volunteer_response.json()["can_manage_shifts"] is False

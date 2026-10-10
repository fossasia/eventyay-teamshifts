import json
import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
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
def location(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Main Hall")


@pytest.fixture
def shift(event, team_role, location):
    with scope(event=event):
        shift = Shift.objects.create(
            event=event,
            name="Morning Shift",
            location=location,
            start_time=now(),
            end_time=now() + timedelta(hours=3),
        )
        ShiftRoleAssignment.objects.create(shift=shift, role=team_role, capacity=2)
        return shift


@pytest.mark.django_db
def test_organizer_can_edit_shift_via_public_manage_url(orga_client, event, shift, team_role, location):
    url = reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    payload = {
        "title": {"en": "Updated Shift Name"},
        "description": "",
        "start": shift.start_time.isoformat(),
        "end": shift.end_time.isoformat(),
        "room": location.pk,
        "roles": [{"id": team_role.pk, "capacity": 3}],
    }
    response = orga_client.patch(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["talk"]["title"]["en"] == "Updated Shift Name"
    assert body["talk"]["room"] == location.pk
    assert body["talk"]["roles"][0]["capacity"] == 3

    with scope(event=event):
        shift.refresh_from_db()
        assert shift.name == "Updated Shift Name"
        assert shift.location_id == location.pk
        assert shift.role_assignments.get(role=team_role).capacity == 3


@pytest.mark.django_db
def test_partial_update_without_room_keeps_shift_location(orga_client, event, shift, team_role, location):
    url = reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    payload = {"title": {"en": "Renamed"}, "roles": [{"id": team_role.pk, "capacity": 5}]}
    response = orga_client.patch(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    with scope(event=event):
        shift.refresh_from_db()
        assert shift.name == "Renamed"
        assert shift.location_id == location.pk


@pytest.mark.django_db
def test_explicit_null_room_without_start_unschedules_shift(orga_client, event, shift, location):
    url = reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    response = orga_client.patch(url, data=json.dumps({"room": None, "title": {"en": "Morning Shift"}}), content_type="application/json")

    assert response.status_code == 200
    with scope(event=event):
        shift.refresh_from_db()
        assert shift.location_id is None


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
def test_assigning_via_public_url_queues_the_organizer_assignment_email(orga_client, event, shift, team_role, volunteer, member_client):
    with scope(event=event):
        CallForTeamMembers.objects.create(event=event, active=True, shift_schedule_published=True)
    url = reverse(
        "plugins:teamshifts:public_shift_assignments",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    payload = {"shift_id": shift.pk, "user_id": volunteer.pk, "role_id": team_role.pk}
    with TestCase().captureOnCommitCallbacks(execute=True):
        response = orga_client.post(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    with scope(event=event):
        queued = TeamShiftsEmailQueue.objects.get(event=event, shift=shift)
        assert queued.shift_role_id == team_role.pk
        assert list(TeamShiftsEmailQueueRecipient.objects.filter(queue=queued).values_list("user_id", flat=True)) == [volunteer.pk]


@pytest.mark.django_db
def test_assigning_locks_shift_then_role_assignment_like_the_claim_path(orga_client, event, shift, team_role, volunteer, member_client):
    url = reverse(
        "plugins:teamshifts:public_shift_assignments",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    payload = {"shift_id": shift.pk, "user_id": volunteer.pk, "role_id": team_role.pk}

    with CaptureQueriesContext(connection) as queries:
        response = orga_client.post(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    locked_tables = [re.search(r'FROM "(\w+)"', query["sql"]).group(1) for query in queries if "FOR UPDATE" in query["sql"]]
    assert locked_tables[:2] == ["teamshifts_shift", "teamshifts_shiftroleassignment"]


@pytest.mark.django_db
def test_assigning_beyond_role_capacity_is_rejected(orga_client, event, shift, team_role):
    url = reverse(
        "plugins:teamshifts:public_shift_assignments",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    members = []
    with scope(event=event):
        for index in range(3):
            member = User.objects.create_user(email=f"member{index}@example.com", password="password")
            TeamMemberApplication.objects.create(event=event, user=member, status=ApplicationStatus.ACCEPTED)
            members.append(member)

    statuses = []
    for member in members:
        payload = {"shift_id": shift.pk, "user_id": member.pk, "role_id": team_role.pk}
        statuses.append(orga_client.post(url, data=json.dumps(payload), content_type="application/json").status_code)

    assert statuses == [200, 200, 400]
    with scope(event=event):
        assert ShiftAssignment.objects.filter(shift=shift, role=team_role).count() == 2


@pytest.mark.django_db
def test_organizer_can_assign_themselves_via_public_assignments_url(orga_client, event, user, shift, team_role):
    with scope(event=event):
        TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED)
    url = reverse(
        "plugins:teamshifts:public_shift_assignments",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    payload = {"shift_id": shift.pk, "user_id": user.pk, "role_id": team_role.pk}
    response = orga_client.post(url, data=json.dumps(payload), content_type="application/json")

    assert response.status_code == 200
    assignee = next(entry for role in response.json()["roles"] for entry in role["assigned"] if entry["id"] == user.pk)
    assert assignee["self_assigned"] is False
    assert assignee["assigned_by_name"]

    with scope(event=event):
        assignment = ShiftAssignment.objects.get(shift=shift, team_member=user)
        assert assignment.role_id == team_role.pk
        assert assignment.assigned_by_id == user.pk


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

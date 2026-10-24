import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User

from teamshifts.models import Shift, ShiftAssignment, ShiftLocation, ShiftRoleAssignment, TeamRole


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
def team_role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Registration")


@pytest.fixture
def location(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Main Hall")


@pytest.fixture
def full_shift(event, user, team_role, location):
    """A shift whose Registration role (capacity 2) has two volunteers assigned."""
    with scope(event=event):
        shift = Shift.objects.create(
            event=event,
            name="Morning Shift",
            location=location,
            start_time=now() + timedelta(days=1),
            end_time=now() + timedelta(days=1, hours=3),
        )
        ShiftRoleAssignment.objects.create(shift=shift, role=team_role, capacity=2)
        for index in range(2):
            member = User.objects.create_user(email=f"member{index}@example.com", password="password")
            ShiftAssignment.objects.create(shift=shift, team_member=member, role=team_role, assigned_by=user)
        return shift


def manage_url(event, shift):
    return reverse(
        "plugins:teamshifts:public_shift_manage",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )


def edit_payload(shift, location, role, capacity):
    return {
        "title": {"en": "Renamed Shift"},
        "description": "",
        "start": shift.start_time.isoformat(),
        "end": shift.end_time.isoformat(),
        "room": location.pk,
        "roles": [{"id": role.pk, "capacity": capacity}],
    }


@pytest.mark.django_db
def test_lowering_capacity_below_assigned_volunteers_is_rejected(orga_client, event, full_shift, team_role, location):
    response = orga_client.patch(
        manage_url(event, full_shift),
        data=json.dumps(edit_payload(full_shift, location, team_role, 1)),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "2 volunteers are assigned to Registration. Unassign volunteers before lowering its capacity to 1."
    with scope(event=event):
        full_shift.refresh_from_db()
        assert full_shift.name == "Morning Shift"
        assert full_shift.role_assignments.get(role=team_role).capacity == 2
        assert full_shift.assignments.count() == 2


@pytest.mark.django_db
def test_legacy_role_payload_cannot_lower_capacity_below_assigned_volunteers(orga_client, event, full_shift, team_role):
    response = orga_client.patch(
        manage_url(event, full_shift),
        data=json.dumps({"role": team_role.pk, "capacity": 1}),
        content_type="application/json",
    )

    assert response.status_code == 400
    with scope(event=event):
        assert full_shift.role_assignments.get(role=team_role).capacity == 2


@pytest.mark.django_db
def test_capacity_can_be_lowered_to_the_number_of_assigned_volunteers(orga_client, event, full_shift, team_role, location):
    with scope(event=event):
        full_shift.role_assignments.filter(role=team_role).update(capacity=3)

    response = orga_client.patch(
        manage_url(event, full_shift),
        data=json.dumps(edit_payload(full_shift, location, team_role, 2)),
        content_type="application/json",
    )

    assert response.status_code == 200
    with scope(event=event):
        assert full_shift.role_assignments.get(role=team_role).capacity == 2
        assert full_shift.assignments.count() == 2


@pytest.mark.django_db
def test_shift_edit_form_rejects_capacity_below_assigned_volunteers(orga_client, event, full_shift, team_role, location):
    with scope(event=event):
        role_assignment = full_shift.role_assignments.get(role=team_role)
    url = reverse(
        "plugins:teamshifts:shift_edit",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": full_shift.pk},
    )
    data = {
        "mode": "single",
        "name": "Morning Shift",
        "location": location.pk,
        "start_time": full_shift.start_time.strftime("%Y-%m-%dT%H:%M"),
        "end_time": full_shift.end_time.strftime("%Y-%m-%dT%H:%M"),
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "1",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-id": role_assignment.pk,
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }

    response = orga_client.post(url, data)

    assert response.status_code == 200
    assert "2 volunteers are assigned to Registration." in response.content.decode()
    with scope(event=event):
        role_assignment.refresh_from_db()
        assert role_assignment.capacity == 2


@pytest.mark.django_db
def test_already_overfilled_shift_can_still_be_edited_via_api(orga_client, event, full_shift, team_role, location):
    with scope(event=event):
        full_shift.role_assignments.filter(role=team_role).update(capacity=1)

    response = orga_client.patch(
        manage_url(event, full_shift),
        data=json.dumps(edit_payload(full_shift, location, team_role, 1)),
        content_type="application/json",
    )

    assert response.status_code == 200
    with scope(event=event):
        full_shift.refresh_from_db()
        assert full_shift.name == "Renamed Shift"
        assert full_shift.role_assignments.get(role=team_role).capacity == 1


@pytest.mark.django_db
def test_already_overfilled_shift_can_still_be_edited_via_form(orga_client, event, full_shift, team_role, location):
    with scope(event=event):
        full_shift.role_assignments.filter(role=team_role).update(capacity=1)
        role_assignment = full_shift.role_assignments.get(role=team_role)
    url = reverse(
        "plugins:teamshifts:shift_edit",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": full_shift.pk},
    )
    data = {
        "mode": "single",
        "name": "Renamed Shift",
        "location": location.pk,
        "start_time": full_shift.start_time.strftime("%Y-%m-%dT%H:%M"),
        "end_time": full_shift.end_time.strftime("%Y-%m-%dT%H:%M"),
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "1",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-id": role_assignment.pk,
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }

    response = orga_client.post(url, data)

    assert response.status_code == 302
    with scope(event=event):
        full_shift.refresh_from_db()
        assert full_shift.name == "Renamed Shift"

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
    Shift,
    ShiftAssignment,
    ShiftLocation,
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
    assert response.status_code == 302
    assert response.url == reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    with scope(event=event):
        assert Shift.objects.count() == 2


@pytest.mark.django_db
def test_shift_create_repeating_rejects_more_than_cap(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=51)  # 51 one-hour shifts

    data = {
        "mode": "repeating",
        "name": "Too many",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": "60",
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 200
    assert b"The maximum allowed is 50 per action" in response.content
    with scope(event=event):
        assert Shift.objects.count() == 0


@pytest.mark.django_db
def test_shift_create_repeating_allows_exactly_cap(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=50)  # 50 one-hour shifts

    data = {
        "mode": "repeating",
        "name": "At cap",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": "60",
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 302
    with scope(event=event):
        assert Shift.objects.count() == 50


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
        "roles-TOTAL_FORMS": "0",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
    }

    response = orga_client.post(url, data)
    assert response.status_code == 200
    if b"At least one role must be added to the shift" not in response.content:
        print("FORMSET ERRORS:", response.context_data["formset"].errors)
        print("FORMSET NON-FORM ERRORS:", response.context_data["formset"].non_form_errors())
    assert b"At least one role must be added to the shift" in response.content

    with scope(event=event):
        assert Shift.objects.count() == 0


@pytest.mark.django_db
def test_team_lead_can_assign_member_within_scope(event, user, team_role, settings):
    settings.SITE_URL = "https://testserver"
    lead = User.objects.create_user(
        email="lead@example.com",
        password="secret",
    )
    member = User.objects.create_user(
        email="member@example.com",
        password="secret",
    )

    with scope(event=event):
        TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
        )

        Team.objects.create(
            organizer=event.organizer,
            name="Lead Team",
            teamshifts_role="lead",
            all_events=True,
            limit_teamshifts_roles=[team_role.pk],
        ).members.add(lead)

        shift = Shift.objects.create(
            event=event,
            name="Test Shift",
            start_time=now() + timedelta(days=1),
            end_time=now() + timedelta(days=1, hours=2),
        )
        ShiftRoleAssignment.objects.create(
            shift=shift,
            role=team_role,
            capacity=1,
        )

    client = Client()
    client.force_login(lead)

    url = reverse(
        "plugins:teamshifts:api_assignments",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.post(
        url,
        data=json.dumps(
            {
                "shift_id": shift.pk,
                "user_id": member.pk,
                "role_id": team_role.pk,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200

    with scope(event=event):
        assert ShiftAssignment.objects.filter(
            shift=shift,
            team_member=member,
            role=team_role,
        ).exists()


@pytest.mark.django_db
def test_team_lead_cannot_assign_member_outside_scope(event, user, settings):
    settings.SITE_URL = "https://testserver"
    lead = User.objects.create_user(
        email="lead@example.com",
        password="secret",
    )
    member = User.objects.create_user(
        email="member@example.com",
        password="secret",
    )

    with scope(event=event):
        allowed_role = TeamRole.objects.create(
            event=event,
            name="Allowed Role",
        )
        restricted_role = TeamRole.objects.create(
            event=event,
            name="Restricted Role",
        )

        TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
        )

        Team.objects.create(
            organizer=event.organizer,
            name="Lead Team",
            teamshifts_role="lead",
            all_events=True,
            limit_teamshifts_roles=[allowed_role.pk],
        ).members.add(lead)

        shift = Shift.objects.create(
            event=event,
            name="Test Shift",
            start_time=now() + timedelta(days=1),
            end_time=now() + timedelta(days=1, hours=2),
        )
        ShiftRoleAssignment.objects.create(
            shift=shift,
            role=restricted_role,
            capacity=1,
        )

    client = Client()
    client.force_login(lead)

    url = reverse(
        "plugins:teamshifts:api_assignments",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.post(
        url,
        data=json.dumps(
            {
                "shift_id": shift.pk,
                "user_id": member.pk,
                "role_id": restricted_role.pk,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert b"You cannot assign members to this role." in response.content

    with scope(event=event):
        assert not ShiftAssignment.objects.filter(
            shift=shift,
            team_member=member,
        ).exists()


@pytest.mark.django_db
def test_team_lead_can_unassign_member_within_scope(event, user, team_role, settings):
    settings.SITE_URL = "https://testserver"

    lead = User.objects.create_user(
        email="lead@example.com",
        password="secret",
    )
    member = User.objects.create_user(
        email="member@example.com",
        password="secret",
    )

    with scope(event=event):
        TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
        )
        Team.objects.create(
            organizer=event.organizer,
            name="Lead Team",
            teamshifts_role="lead",
            all_events=True,
            limit_teamshifts_roles=[team_role.pk],
        ).members.add(lead)

        shift = Shift.objects.create(
            event=event,
            name="Test Shift",
            start_time=now() + timedelta(days=1),
            end_time=now() + timedelta(days=1, hours=2),
        )
        ShiftRoleAssignment.objects.create(
            shift=shift,
            role=team_role,
            capacity=1,
        )
        ShiftAssignment.objects.create(
            shift=shift,
            team_member=member,
            role=team_role,
            assigned_by=lead,
        )

    client = Client()
    client.force_login(lead)

    url = reverse(
        "plugins:teamshifts:api_assignments",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.delete(
        f"{url}?shift_id={shift.pk}&user_id={member.pk}&role_id={team_role.pk}",
    )

    assert response.status_code == 200

    with scope(event=event):
        assert not ShiftAssignment.objects.filter(
            shift=shift,
            team_member=member,
            role=team_role,
        ).exists()


@pytest.mark.django_db
def test_team_lead_cannot_unassign_member_outside_scope(event, user, settings):
    settings.SITE_URL = "https://testserver"

    lead = User.objects.create_user(
        email="lead@example.com",
        password="secret",
    )
    member = User.objects.create_user(
        email="member@example.com",
        password="secret",
    )

    with scope(event=event):
        allowed_role = TeamRole.objects.create(
            event=event,
            name="Allowed Role",
        )
        restricted_role = TeamRole.objects.create(
            event=event,
            name="Restricted Role",
        )

        TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
        )
        Team.objects.create(
            organizer=event.organizer,
            name="Lead Team",
            teamshifts_role="lead",
            all_events=True,
            limit_teamshifts_roles=[allowed_role.pk],
        ).members.add(lead)

        shift = Shift.objects.create(
            event=event,
            name="Test Shift",
            start_time=now() + timedelta(days=1),
            end_time=now() + timedelta(days=1, hours=2),
        )
        ShiftRoleAssignment.objects.create(
            shift=shift,
            role=restricted_role,
            capacity=1,
        )
        ShiftAssignment.objects.create(
            shift=shift,
            team_member=member,
            role=restricted_role,
            assigned_by=lead,
        )

    client = Client()
    client.force_login(lead)

    url = reverse(
        "plugins:teamshifts:api_assignments",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.delete(
        f"{url}?shift_id={shift.pk}&user_id={member.pk}&role_id={restricted_role.pk}",
    )

    assert response.status_code == 400
    assert b"You cannot unassign members from this role." in response.content

    with scope(event=event):
        assert ShiftAssignment.objects.filter(
            shift=shift,
            team_member=member,
            role=restricted_role,
        ).exists()

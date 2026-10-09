import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User

from teamshifts.forms import count_shifts, split_shift_range
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


def _repeating_data(location, team_role, start, end, length):
    return {
        "mode": "repeating",
        "name": "Rep Shift",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M"),
        "shift_length_minutes": str(length),
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": team_role.pk,
        "roles-0-capacity": "1",
    }


@pytest.mark.django_db
def test_shift_create_repeating_with_shorter_last_shift(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = (now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start.replace(hour=18)  # 09:00-18:00, 540 minutes

    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 120))
    assert response.status_code == 302

    with scope(event=event):
        shifts = list(Shift.objects.order_by("start_time"))
        assert [(s.start_time.hour, s.end_time.hour) for s in shifts] == [(9, 11), (11, 13), (13, 15), (15, 17), (17, 18)]
        assert shifts[-1].end_time == end
        assert ShiftRoleAssignment.objects.count() == 5


@pytest.mark.django_db
def test_shift_create_repeating_length_longer_than_range(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=1)

    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 90))
    assert response.status_code == 200
    assert b"The shift length is longer than the time between start and end." in response.content
    assert b"divide evenly" not in response.content

    with scope(event=event):
        assert Shift.objects.count() == 0


@pytest.mark.django_db
def test_shift_create_repeating_shorter_last_shift_counts_toward_cap(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=49, minutes=30)  # 49 full one-hour shifts + one 30-minute shift = 50

    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 60))
    assert response.status_code == 302
    with scope(event=event):
        assert Shift.objects.count() == 50

    response = orga_client.post(url, _repeating_data(location, team_role, start, end + timedelta(hours=1), 60))
    assert response.status_code == 200
    assert b"The maximum allowed is 50 per action" in response.content


@pytest.mark.django_db
def test_shift_create_repeating_cap_checked_before_splitting(orga_client, event, location, team_role, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("split_shift_range must not run for a range over the cap")

    monkeypatch.setattr("teamshifts.forms.split_shift_range", fail)
    monkeypatch.setattr("teamshifts.views.split_shift_range", fail)
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(days=3650)  # ten years of one-minute shifts

    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 1))
    assert response.status_code == 200
    assert b"The maximum allowed is 50 per action" in response.content


@pytest.mark.django_db
def test_shift_create_repeating_huge_length_rejected(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=2)

    # Adding this many minutes to a datetime overflows past year 9999.
    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 10**10))
    assert response.status_code == 200
    assert b"The shift length is longer than the time between start and end." in response.content


def test_split_shift_range_exact_and_remainder():
    start = now().replace(hour=9, minute=0, second=0, microsecond=0)
    assert split_shift_range(start, start + timedelta(hours=2), 60) == [
        (start, start + timedelta(hours=1)),
        (start + timedelta(hours=1), start + timedelta(hours=2)),
    ]
    assert split_shift_range(start, start + timedelta(minutes=100), 60) == [
        (start, start + timedelta(hours=1)),
        (start + timedelta(hours=1), start + timedelta(minutes=100)),
    ]


def test_split_shift_range_merges_leftover_under_15_minutes():
    start = now().replace(hour=9, minute=0, second=0, microsecond=0)
    # 14 minutes left over: added to the last full shift.
    assert split_shift_range(start, start + timedelta(minutes=134), 60) == [
        (start, start + timedelta(hours=1)),
        (start + timedelta(hours=1), start + timedelta(minutes=134)),
    ]
    # Exactly 15 minutes left over: kept as its own shift.
    assert split_shift_range(start, start + timedelta(minutes=135), 60)[-1] == (start + timedelta(hours=2), start + timedelta(minutes=135))


def test_count_shifts_matches_split_shift_range():
    start = now().replace(hour=9, minute=0, second=0, microsecond=0)
    for minutes, length in [(120, 60), (100, 60), (134, 60), (135, 60), (481, 60), (540, 120), (45, 30), (40, 30)]:
        end = start + timedelta(minutes=minutes)
        assert count_shifts(start, end, length) == len(split_shift_range(start, end, length)), (minutes, length)


@pytest.mark.django_db
def test_shift_create_repeating_merges_tiny_last_shift(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = (now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start.replace(hour=17, minute=1)  # 09:00-17:01 in 1-hour shifts

    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 60))
    assert response.status_code == 302

    with scope(event=event):
        shifts = list(Shift.objects.order_by("start_time"))
        assert len(shifts) == 8
        assert shifts[-1].start_time == start.replace(hour=16)
        assert shifts[-1].end_time == end


@pytest.mark.django_db
def test_shift_create_repeating_merged_leftover_does_not_count_toward_cap(orga_client, event, location, team_role):
    url = reverse("plugins:teamshifts:shift_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    start = now() + timedelta(days=1)
    end = start + timedelta(hours=50, minutes=10)  # 50 full shifts, 10 minutes added to the last one

    response = orga_client.post(url, _repeating_data(location, team_role, start, end, 60))
    assert response.status_code == 302
    with scope(event=event):
        assert Shift.objects.count() == 50


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


@pytest.mark.django_db
def test_team_lead_cannot_assign_member_with_invalid_role_id(event, user, team_role, settings):
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
                "role_id": "abc",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert b"Invalid role_id." in response.content
    with scope(event=event):
        assert not ShiftAssignment.objects.filter(
            shift=shift,
            team_member=member,
            role=team_role,
        ).exists()

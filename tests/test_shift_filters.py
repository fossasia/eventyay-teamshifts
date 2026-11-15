import zoneinfo
from datetime import datetime, timedelta

import pytest
from django.urls import reverse
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
def filter_setup(event):
    tz = zoneinfo.ZoneInfo(event.timezone)
    with scope(event=event):
        loc_a = ShiftLocation.objects.create(event=event, name="Alpha Stage", position=1)
        loc_b = ShiftLocation.objects.create(event=event, name="Beta Room", position=2)
        loc_empty = ShiftLocation.objects.create(event=event, name="Gamma Unused", position=3)

        role_usher = TeamRole.objects.create(event=event, name="Usher")
        role_lead = TeamRole.objects.create(event=event, name="Stage Lead")
        role_unused = TeamRole.objects.create(event=event, name="Unused Role")

        vol1 = User.objects.create_user(email="vol1@example.com", password="x")
        vol2 = User.objects.create_user(email="vol2@example.com", password="x")

        d1_morning = datetime(2026, 6, 15, 9, 0, tzinfo=tz)
        d1_afternoon = datetime(2026, 6, 15, 14, 0, tzinfo=tz)
        d2_morning = datetime(2026, 6, 16, 10, 0, tzinfo=tz)

        shift1 = Shift.objects.create(
            event=event,
            name="Morning Setup",
            location=loc_a,
            start_time=d1_morning,
            end_time=d1_morning + timedelta(hours=3),
        )
        ShiftRoleAssignment.objects.create(shift=shift1, role=role_usher, capacity=2)
        ShiftAssignment.objects.create(shift=shift1, team_member=vol1, role=role_usher)

        shift2 = Shift.objects.create(
            event=event,
            name="Afternoon Info Desk",
            location=loc_b,
            start_time=d1_afternoon,
            end_time=d1_afternoon + timedelta(hours=2),
        )
        ShiftRoleAssignment.objects.create(shift=shift2, role=role_lead, capacity=1)
        ShiftAssignment.objects.create(shift=shift2, team_member=vol2, role=role_lead)

        shift3 = Shift.objects.create(
            event=event,
            name="Day 2 Teardown",
            location=loc_a,
            start_time=d2_morning,
            end_time=d2_morning + timedelta(hours=4),
        )
        ShiftRoleAssignment.objects.create(shift=shift3, role=role_usher, capacity=1)

        shift4 = Shift.objects.create(
            event=event,
            name="Ad-hoc Shift",
            location=loc_b,
            start_time=d2_morning + timedelta(hours=1),
            end_time=d2_morning + timedelta(hours=2),
        )

        return {
            "loc_a": loc_a,
            "loc_b": loc_b,
            "loc_empty": loc_empty,
            "role_usher": role_usher,
            "role_lead": role_lead,
            "role_unused": role_unused,
            "shift1": shift1,
            "shift2": shift2,
            "shift3": shift3,
            "shift4": shift4,
            "day1": "2026-06-15",
            "day2": "2026-06-16",
        }


@pytest.mark.django_db
def test_filter_by_search_query(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?q=setup")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 1
    assert shifts[0].pk == filter_setup["shift1"].pk

    response = orga_client.get(f"{url}?q=AFTERNOON")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 1
    assert shifts[0].pk == filter_setup["shift2"].pk

    response = orga_client.get(f"{url}?q=Nonexistent")
    assert len(response.context["shifts"]) == 0


@pytest.mark.django_db
def test_filter_by_day_and_day_counts(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?day={filter_setup['day1']}")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 2
    assert {s.pk for s in shifts} == {filter_setup["shift1"].pk, filter_setup["shift2"].pk}

    response = orga_client.get(f"{url}?day={filter_setup['day2']}")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 2
    assert {s.pk for s in shifts} == {filter_setup["shift3"].pk, filter_setup["shift4"].pk}

    days = response.context["days"]
    assert len(days) == 2
    assert days[0]["value"] == "2026-06-15"
    assert "2 shifts" in days[0]["label"]
    assert days[1]["value"] == "2026-06-16"
    assert "2 shifts" in days[1]["label"]


@pytest.mark.django_db
def test_filter_by_location(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?location={filter_setup['loc_a'].pk}")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 2
    assert {s.pk for s in shifts} == {filter_setup["shift1"].pk, filter_setup["shift3"].pk}

    locations = response.context["locations"]
    loc_ids = [loc.pk for loc in locations]
    assert filter_setup["loc_a"].pk in loc_ids
    assert filter_setup["loc_b"].pk in loc_ids
    assert filter_setup["loc_empty"].pk not in loc_ids


@pytest.mark.django_db
def test_filter_by_role(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?role={filter_setup['role_lead'].pk}")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 1
    assert shifts[0].pk == filter_setup["shift2"].pk

    response = orga_client.get(f"{url}?role={filter_setup['role_usher'].pk}")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 2
    assert {s.pk for s in shifts} == {filter_setup["shift1"].pk, filter_setup["shift3"].pk}


@pytest.mark.django_db
def test_filter_by_staffing(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?staffing=open")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 2
    assert {s.pk for s in shifts} == {filter_setup["shift1"].pk, filter_setup["shift3"].pk}

    response = orga_client.get(f"{url}?staffing=full")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 1
    assert shifts[0].pk == filter_setup["shift2"].pk

    response = orga_client.get(url)
    shifts = list(response.context["shifts"])
    assert len(shifts) == 4
    assert filter_setup["shift4"].pk in [s.pk for s in shifts]


@pytest.mark.django_db
def test_combined_filters(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?day={filter_setup['day1']}&location={filter_setup['loc_a'].pk}&staffing=open")
    shifts = list(response.context["shifts"])
    assert len(shifts) == 1
    assert shifts[0].pk == filter_setup["shift1"].pk


@pytest.mark.django_db
def test_active_filter_chips_and_counts(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?q=Setup&staffing=open")
    ctx = response.context
    assert ctx["is_filtered"] is True
    assert ctx["total_shifts_count"] == 4
    assert ctx["matching_shifts_count"] == 1

    chips = ctx["active_filter_chips"]
    assert len(chips) == 2
    param_names = [c["remove_param"] for c in chips]
    assert "q" in param_names
    assert "staffing" in param_names


@pytest.mark.django_db
def test_shifts_sorting(orga_client, event, filter_setup):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    response = orga_client.get(f"{url}?sort=name")
    names = [s.name for s in response.context["shifts"]]
    assert names == sorted(names)

    response = orga_client.get(f"{url}?sort=-name")
    names = [s.name for s in response.context["shifts"]]
    assert names == sorted(names, reverse=True)

    response = orga_client.get(f"{url}?sort=-start_time")
    shifts = list(response.context["shifts"])
    for i in range(len(shifts) - 1):
        assert shifts[i].start_time >= shifts[i + 1].start_time


@pytest.mark.django_db
def test_delete_preserves_query_string(orga_client, event, filter_setup):
    shift = filter_setup["shift1"]
    delete_url = reverse(
        "plugins:teamshifts:shift_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    response = orga_client.post(f"{delete_url}?q=Setup&day=2026-06-15")
    assert response.status_code == 302
    expected_redirect = reverse(
        "plugins:teamshifts:shifts",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    assert response["Location"] == f"{expected_redirect}?q=Setup&day=2026-06-15"


@pytest.mark.django_db
def test_bulk_delete_preserves_query_string(orga_client, event, filter_setup):
    bulk_url = reverse(
        "plugins:teamshifts:shift_bulk_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    to_delete = [filter_setup["shift2"].pk]
    response = orga_client.post(f"{bulk_url}?location={filter_setup['loc_b'].pk}&staffing=full", {"shift_ids": to_delete})
    assert response.status_code == 302
    expected_redirect = reverse(
        "plugins:teamshifts:shifts",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    assert response["Location"] == f"{expected_redirect}?location={filter_setup['loc_b'].pk}&staffing=full"

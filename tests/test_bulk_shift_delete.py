from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Team

from teamshifts.models import Shift, ShiftLocation


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
def non_orga_client(client, django_user_model, settings):
    settings.SITE_URL = "https://testserver"
    non_orga = django_user_model.objects.create_user(email="other@example.com", password="x")
    client.force_login(non_orga)
    return client


@pytest.fixture
def other_event(event):
    with scope(event=event):
        return Event.objects.create(
            organizer=event.organizer,
            name="Other Event",
            slug="other-event",
            date_from=now(),
            plugins="teamshifts",
        )


@pytest.fixture
def location(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Stage A")


@pytest.fixture
def shifts(event, location):
    with scope(event=event):
        created = []
        base_time = now() + timedelta(days=1)
        for i in range(5):
            start = base_time + timedelta(hours=i * 2)
            end = start + timedelta(hours=2)
            shift = Shift.objects.create(
                event=event,
                name=f"Shift {i}",
                location=location,
                start_time=start,
                end_time=end,
            )
            created.append(shift)
        return created


@pytest.mark.django_db
def test_bulk_delete_shifts_success(orga_client, event, shifts):
    url = reverse(
        "plugins:teamshifts:shift_bulk_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    to_delete = [shifts[0].pk, shifts[1].pk, shifts[2].pk]
    response = orga_client.post(url, {"shift_ids": to_delete})

    assert response.status_code == 302
    assert response["Location"] == reverse(
        "plugins:teamshifts:shifts",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    with scope(event=event):
        remaining = list(Shift.objects.filter(event=event).values_list("pk", flat=True))
        assert set(remaining) == {shifts[3].pk, shifts[4].pk}


@pytest.mark.django_db
def test_bulk_delete_no_selection_warning(orga_client, event, shifts):
    url = reverse(
        "plugins:teamshifts:shift_bulk_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = orga_client.post(url, {"shift_ids": []}, follow=True)

    assert response.status_code == 200
    with scope(event=event):
        assert Shift.objects.filter(event=event).count() == 5


@pytest.mark.django_db
def test_bulk_delete_permission_denied(non_orga_client, event, shifts):
    url = reverse(
        "plugins:teamshifts:shift_bulk_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = non_orga_client.post(url, {"shift_ids": [shifts[0].pk]})
    assert response.status_code in (403, 404)

    with scope(event=event):
        assert Shift.objects.filter(event=event).count() == 5


@pytest.mark.django_db
def test_bulk_delete_cross_event_isolation(orga_client, event, location, other_event):
    with scope(event=other_event):
        other_location = ShiftLocation.objects.create(event=other_event, name="Other Stage")
        start = now() + timedelta(days=1)
        other_shift = Shift.objects.create(
            event=other_event,
            name="Other Event Shift",
            location=other_location,
            start_time=start,
            end_time=start + timedelta(hours=2),
        )

    url = reverse(
        "plugins:teamshifts:shift_bulk_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = orga_client.post(url, {"shift_ids": [other_shift.pk]})
    assert response.status_code == 302

    with scope(event=other_event):
        assert Shift.objects.filter(event=other_event, pk=other_shift.pk).exists()


@pytest.mark.django_db
def test_shifts_list_pagination(orga_client, event, location):
    with scope(event=event):
        base_time = now() + timedelta(days=1)
        shifts_to_create = [
            Shift(
                event=event,
                name=f"Paginated Shift {i}",
                location=location,
                start_time=base_time + timedelta(hours=i),
                end_time=base_time + timedelta(hours=i + 1),
            )
            for i in range(55)
        ]
        Shift.objects.bulk_create(shifts_to_create)

    url = reverse(
        "plugins:teamshifts:shifts",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = orga_client.get(url)
    assert response.status_code == 200
    assert len(response.context["shifts"]) == 50
    assert response.context["is_paginated"] is True
    assert response.context["total_shift_count"] == 55

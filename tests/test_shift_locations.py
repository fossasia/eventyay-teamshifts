from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Organizer, Team

from teamshifts.models import Shift, ShiftLocation, ShiftRoleAssignment, TeamRole


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
        return ShiftLocation.objects.create(event=event, name="Main Hall", description="The big hall")


@pytest.fixture
def team_role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Volunteer")


@pytest.mark.django_db
def test_location_list_requires_login(client, event, settings):
    settings.SITE_URL = "https://testserver"
    url = reverse("plugins:teamshifts:locations", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.get(url)
    assert response.status_code == 302
    assert "login" in response["Location"].lower()


@pytest.mark.django_db
def test_location_list_shows_locations(orga_client, event, location):
    url = reverse("plugins:teamshifts:locations", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = orga_client.get(url)
    assert response.status_code == 200
    assert b"Main Hall" in response.content


@pytest.mark.django_db
def test_location_create_get(orga_client, event):
    url = reverse("plugins:teamshifts:location_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = orga_client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_location_create_post(orga_client, event):
    url = reverse("plugins:teamshifts:location_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = orga_client.post(url, {"name": "Stage Area", "description": "Near the stage"})
    assert response.status_code == 302
    with scope(event=event):
        assert ShiftLocation.objects.filter(event=event, name="Stage Area").exists()


@pytest.mark.django_db
def test_location_create_duplicate_name_shows_form_error(orga_client, event, location):
    url = reverse("plugins:teamshifts:location_create", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = orga_client.post(url, {"name": "Main Hall", "description": "Duplicate"})
    assert response.status_code == 200
    with scope(event=event):
        assert ShiftLocation.objects.filter(event=event, name="Main Hall").count() == 1


@pytest.mark.django_db
def test_location_update_get(orga_client, event, location):
    url = reverse(
        "plugins:teamshifts:location_edit",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": location.pk},
    )
    response = orga_client.get(url)
    assert response.status_code == 200
    assert b"Main Hall" in response.content


@pytest.mark.django_db
def test_location_update_post(orga_client, event, location):
    url = reverse(
        "plugins:teamshifts:location_edit",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": location.pk},
    )
    response = orga_client.post(url, {"name": "Renamed Hall", "description": "Updated"})
    assert response.status_code == 302
    with scope(event=event):
        location.refresh_from_db()
        assert location.name == "Renamed Hall"


@pytest.mark.django_db
def test_location_delete_get(orga_client, event, location):
    url = reverse(
        "plugins:teamshifts:location_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": location.pk},
    )
    response = orga_client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_location_delete_success(orga_client, event, location):
    url = reverse(
        "plugins:teamshifts:location_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": location.pk},
    )
    response = orga_client.post(url)
    assert response.status_code == 302
    with scope(event=event):
        assert not ShiftLocation.objects.filter(pk=location.pk).exists()


@pytest.mark.django_db
def test_location_delete_blocked_when_in_use(orga_client, event, location, team_role):
    with scope(event=event):
        shift = Shift.objects.create(
            event=event,
            location=location,
            start_time=now(),
            end_time=now() + timedelta(hours=2),
        )
        ShiftRoleAssignment.objects.create(shift=shift, role=team_role, capacity=1)
    url = reverse(
        "plugins:teamshifts:location_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": location.pk},
    )
    response = orga_client.post(url)
    assert response.status_code == 302
    with scope(event=event):
        assert ShiftLocation.objects.filter(pk=location.pk).exists()


@pytest.mark.django_db
def test_location_delete_other_event_returns_404(orga_client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    other_organizer = Organizer.objects.create(name="Other Org", slug="other-org")
    other_event = Event.objects.create(
        organizer=other_organizer,
        name="Other Event",
        slug="other-event",
        live=True,
        date_from=now(),
        plugins="teamshifts",
    )
    with scope(event=other_event):
        other_location = ShiftLocation.objects.create(event=other_event, name="Other Hall")
    url = reverse(
        "plugins:teamshifts:location_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": other_location.pk},
    )
    response = orga_client.post(url)
    assert response.status_code == 404

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team

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
def location(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Stage A")


@pytest.fixture
def shift(event, location):
    with scope(event=event):
        start = now()
        return Shift.objects.create(
            event=event,
            name="Test Shift",
            location=location,
            start_time=start,
            end_time=start + timedelta(hours=2),
        )


@pytest.mark.django_db
def test_create_shift_page_renders_empty_role_form(orga_client, event, location):
    url = reverse(
        "plugins:teamshifts:shift_create",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    response = orga_client.get(url)

    assert response.status_code == 200
    assert b'id="role-form-template"' in response.content
    assert b'name="roles-__prefix__-role"' in response.content
    assert b'name="roles-__prefix__-capacity"' in response.content


@pytest.mark.django_db
def test_edit_shift_page_renders_empty_role_form(orga_client, event, shift):
    url = reverse(
        "plugins:teamshifts:shift_edit",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "pk": shift.pk,
        },
    )

    response = orga_client.get(url)

    assert response.status_code == 200
    assert b'id="role-form-template"' in response.content
    assert b'name="roles-__prefix__-role"' in response.content
    assert b'name="roles-__prefix__-capacity"' in response.content

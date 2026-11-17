from datetime import datetime

import pytest
from django.urls import reverse
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


@pytest.mark.django_db
def test_filter_shifts_and_redirect_preserves_query(orga_client, event):
    with scope(event=event):
        loc = ShiftLocation.objects.create(event=event, name="Alpha Stage", position=1)
        shift1 = Shift.objects.create(
            event=event,
            name="Morning Setup",
            location=loc,
            start_time=datetime(2026, 6, 15, 9, 0),
            end_time=datetime(2026, 6, 15, 12, 0),
        )
        Shift.objects.create(
            event=event,
            name="Evening Teardown",
            location=loc,
            start_time=datetime(2026, 6, 15, 18, 0),
            end_time=datetime(2026, 6, 15, 21, 0),
        )

    list_url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    # Test filtering by query
    response = orga_client.get(f"{list_url}?q=Setup")
    assert response.status_code == 200
    shifts = list(response.context["shifts"])
    assert len(shifts) == 1
    assert shifts[0].pk == shift1.pk

    # Test delete preserves filter query parameters and drops page
    delete_url = reverse(
        "plugins:teamshifts:shift_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift1.pk},
    )
    response = orga_client.post(f"{delete_url}?q=Setup&page=2")
    assert response.status_code == 302
    assert response["Location"] == f"{list_url}?q=Setup"

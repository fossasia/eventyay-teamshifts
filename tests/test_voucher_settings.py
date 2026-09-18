import pytest
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import Team


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
def test_voucher_settings_renders_bulk_voucher_link(orga_client, event):
    url = reverse(
        "plugins:teamshifts:voucher_settings",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = orga_client.get(url)

    assert response.status_code == 200

    bulk_voucher_url = reverse(
        "control:event.vouchers.bulk",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    assert bulk_voucher_url in response.content.decode("utf-8")

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, Team, User


@pytest.fixture(autouse=True)
def setup_settings(settings):
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def organizer():
    return Organizer.objects.create(name="Test Organizer", slug="test-org")


@pytest.fixture
def user():
    return User.objects.create_user("user@example.com", "password")


@pytest.mark.django_db
def test_unauthenticated_user_redirects_to_login(client, organizer):
    url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    response = client.get(url)
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_user_with_no_team_membership_returns_404(client, organizer, user):
    client.force_login(user)
    url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    response = client.get(url)
    # Control middleware hides organizers from non-members with 404
    assert response.status_code == 404


@pytest.mark.django_db
def test_user_in_non_teamshifts_team_returns_403(client, organizer, user):
    with scopes_disabled():
        team = Team.objects.create(
            organizer=organizer,
            name="General Team",
            teamshifts_role="",
            can_change_organizer_settings=False,
            all_events=True,
        )
        team.members.add(user)

    client.force_login(user)
    url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    response = client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db
def test_single_event_redirects_to_teamshifts_dashboard(client, organizer, user):
    with scopes_disabled():
        event = Event.objects.create(
            organizer=organizer,
            name="Event 1",
            slug="event-1",
            date_from=now(),
            date_to=now() + timedelta(days=2),
            plugins="teamshifts",
        )
        team = Team.objects.create(
            organizer=organizer,
            name="Volunteer Coordinators",
            teamshifts_role="coordinator",
            all_events=True,
        )
        team.members.add(user)

    client.force_login(user)
    url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    response = client.get(url)
    assert response.status_code == 302
    expected_url = reverse(
        "plugins:teamshifts:dashboard",
        kwargs={"organizer": organizer.slug, "event": event.slug},
    )
    assert response.url == expected_url


@pytest.mark.django_db
def test_multiple_events_renders_selector_page(client, organizer, user):
    with scopes_disabled():
        event1 = Event.objects.create(
            organizer=organizer,
            name="Event 1",
            slug="event-1",
            date_from=now(),
            date_to=now() + timedelta(days=2),
            plugins="teamshifts",
        )
        event2 = Event.objects.create(
            organizer=organizer,
            name="Event 2",
            slug="event-2",
            date_from=now() + timedelta(days=5),
            date_to=now() + timedelta(days=7),
            plugins="teamshifts",
        )
        team = Team.objects.create(
            organizer=organizer,
            name="Volunteer Coordinators",
            teamshifts_role="coordinator",
            all_events=True,
        )
        team.members.add(user)

    client.force_login(user)
    url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    response = client.get(url)
    assert response.status_code == 200
    assert "teamshifts/organizer_landing.html" in [t.name for t in response.templates]
    content = response.content.decode("utf-8")
    assert event1.name in content
    assert event2.name in content


@pytest.mark.django_db
def test_no_events_renders_empty_state(client, organizer, user):
    with scopes_disabled():
        # Event does not have teamshifts plugin
        Event.objects.create(
            organizer=organizer,
            name="Other Event",
            slug="other-event",
            date_from=now(),
            date_to=now() + timedelta(days=2),
            plugins="",
        )
        team = Team.objects.create(
            organizer=organizer,
            name="Volunteer Coordinators",
            teamshifts_role="coordinator",
            all_events=True,
        )
        team.members.add(user)

    client.force_login(user)
    url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    response = client.get(url)
    assert response.status_code == 200
    assert "teamshifts/organizer_landing.html" in [t.name for t in response.templates]
    content = response.content.decode("utf-8")
    assert "No events with TeamShifts enabled were found" in content

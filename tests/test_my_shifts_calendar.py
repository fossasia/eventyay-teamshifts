import pytest
from django.urls import reverse
from django.utils.timezone import now, timedelta
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, User

from teamshifts.models import Shift, ShiftAssignment, ShiftCalendarToken, TeamRole


@pytest.fixture
def user():
    return User.objects.create_user(email="volunteer@example.com", password="password")


@pytest.fixture
def other_user():
    return User.objects.create_user(email="other@example.com", password="password")


@pytest.fixture
def organizer():
    return Organizer.objects.create(name="FOSSASIA", slug="fossasia")


def make_event(organizer, slug, name):
    return Event.objects.create(
        name=name,
        slug=slug,
        organizer=organizer,
        date_from=now(),
        date_to=now() + timedelta(days=2),
        plugins="teamshifts",
    )


@pytest.fixture
def event(organizer):
    return make_event(organizer, "summit", "FOSSASIA Summit")


@pytest.fixture
def second_event(organizer):
    return make_event(organizer, "india", "OpenTechSummit India")


def make_assignment(event, member, name="Setup", role_name="Logistics & Setup", location_text="Main Stage"):
    with scopes_disabled():
        role = TeamRole.objects.create(event=event, name=role_name)
        shift = Shift.objects.create(
            event=event,
            name=name,
            location_text=location_text,
            start_time=now() + timedelta(days=1),
            end_time=now() + timedelta(days=1, hours=2),
        )
        return ShiftAssignment.objects.create(shift=shift, team_member=member, role=role)


@pytest.fixture
def assignment(event, user):
    return make_assignment(event, user)


@pytest.fixture
def token(user):
    return ShiftCalendarToken.objects.create(user=user)


def feed_url(token_value):
    return reverse("plugins:teamshifts:my_shifts_calendar_feed", kwargs={"token": token_value})


@pytest.mark.django_db
def test_feed_works_without_login(client, assignment, token):
    response = client.get(feed_url(token.token))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    body = response.content.decode().replace("\r\n ", "")
    assert "BEGIN:VEVENT" in body
    assert "SUMMARY:Setup – Logistics & Setup" in body
    assert "LOCATION:Main Stage" in body
    assert "FOSSASIA Summit" in body
    assert reverse("plugins:teamshifts:my_shifts_global") in body


@pytest.mark.django_db
def test_feed_unknown_token_is_404(client, assignment, token):
    assert client.get(feed_url("not-a-real-token")).status_code == 404


@pytest.mark.django_db
def test_feed_only_contains_own_shifts(client, event, assignment, token, other_user):
    make_assignment(event, other_user, name="Teardown", role_name="Cleanup")
    body = client.get(feed_url(token.token)).content.decode()
    assert "Setup" in body
    assert "Teardown" not in body


@pytest.mark.django_db
def test_feed_reflects_dropped_and_changed_shifts(client, assignment, token):
    with scopes_disabled():
        shift = assignment.shift
        shift.location_text = "Hall B"
        shift.save(update_fields=["location_text"])
    assert "LOCATION:Hall B" in client.get(feed_url(token.token)).content.decode()
    with scopes_disabled():
        assignment.delete()
    assert "BEGIN:VEVENT" not in client.get(feed_url(token.token)).content.decode()


@pytest.mark.django_db
def test_feed_event_filter(client, event, second_event, user, token):
    make_assignment(event, user, name="Summit Shift")
    make_assignment(second_event, user, name="India Shift")
    body = client.get(feed_url(token.token), {"event": second_event.pk}).content.decode()
    assert "India Shift" in body
    assert "Summit Shift" not in body


@pytest.mark.django_db
def test_download_returns_ics_attachment(client, user, assignment):
    client.force_login(user)
    response = client.get(reverse("plugins:teamshifts:my_shifts_calendar_download"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert "attachment" in response["Content-Disposition"]
    assert "Setup" in response.content.decode()


@pytest.mark.django_db
def test_reset_invalidates_old_link(client, user, assignment, token):
    old_token = token.token
    client.force_login(user)
    response = client.post(reverse("plugins:teamshifts:my_shifts_calendar_reset"))
    assert response.status_code == 302
    token.refresh_from_db()
    assert token.token != old_token
    assert token.regenerated_at is not None
    assert client.get(feed_url(old_token)).status_code == 404
    assert client.get(feed_url(token.token)).status_code == 200


@pytest.mark.django_db
def test_my_shifts_page_shows_calendar_options(client, user, assignment):
    client.force_login(user)
    response = client.get(reverse("plugins:teamshifts:my_shifts_global"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Add to calendar" in body
    assert "Add this event to calendar" in body
    assert "Download .ics file" in body
    assert "Reset link" in body
    assert ShiftCalendarToken.objects.get(user=user).token in body

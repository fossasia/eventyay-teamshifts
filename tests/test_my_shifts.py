import pytest
from django.urls import reverse
from django.utils.timezone import now, timedelta
from eventyay.base.models import Event, Organizer, User

from teamshifts.models import Shift, ShiftAssignment, TeamRole


@pytest.fixture
def user():
    return User.objects.create_user(email="volunteer@example.com", password="password")


@pytest.fixture
def organizer():
    return Organizer.objects.create(name="FOSSASIA", slug="fossasia")


@pytest.fixture
def event(organizer):
    return Event.objects.create(name="FOSSASIA Summit", slug="fossasia-summit", organizer=organizer, date_from=now(), date_to=now() + timedelta(days=2))


@pytest.fixture
def team_role(event):
    return TeamRole.objects.create(event=event, name="Registration")


@pytest.fixture
def shift(event, team_role):
    return Shift.objects.create(event=event, role=team_role, start_time="2026-03-01T09:00:00Z", end_time="2026-03-01T12:00:00Z", capacity=5)


@pytest.fixture
def shift_assignment(shift, user):
    return ShiftAssignment.objects.create(
        shift=shift,
        team_member=user,
    )


@pytest.mark.django_db
def test_my_shifts_unauthenticated(client):
    url = reverse("plugins:teamshifts:my_shifts")
    response = client.get(url)
    assert response.status_code == 302
    assert "/login" in response.url


@pytest.mark.django_db
def test_my_shifts_authenticated(client, user, shift_assignment):
    client.force_login(user)
    url = reverse("plugins:teamshifts:my_shifts")
    response = client.get(url)
    assert response.status_code == 200
    assert b"Registration" in response.content

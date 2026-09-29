import pytest
from django.utils.timezone import now
from eventyay.base.models import Event, Organizer, User


@pytest.fixture
def organizer(db):
    """Create a test organizer."""
    return Organizer.objects.create(name="Test Organizer", slug="test-organizer")


@pytest.fixture
def event(db, organizer):
    """Create a test event with an organizer."""
    event = Event.objects.create(
        organizer=organizer,
        name="Test Event",
        slug="test-event",
        live=True,
        date_from=now(),
        plugins="teamshifts",
    )
    return event


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(email="tester@example.com", password="secret")

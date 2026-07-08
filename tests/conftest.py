import pytest
from django.utils.timezone import now
from eventyay.base.models import Event, Organizer, User


@pytest.fixture
def event(db):
    """Create a test event with an organizer."""
    organizer = Organizer.objects.create(name="Test Organizer", slug="test-organizer")
    event = Event.objects.create(
        organizer=organizer,
        name="Test Event",
        slug="test-event",
        live=True,
        date_from=now(),
    )
    return event


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(email="tester@example.com", password="secret")

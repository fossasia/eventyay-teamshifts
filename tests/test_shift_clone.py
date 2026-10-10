from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Team, User

from teamshifts.models import Shift, ShiftAssignment, ShiftLocation, ShiftRoleAssignment, TeamRole


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
        return ShiftLocation.objects.create(event=event, name="Main Hall")


@pytest.fixture
def roles(event):
    with scope(event=event):
        return [
            TeamRole.objects.create(event=event, name="Volunteer"),
            TeamRole.objects.create(event=event, name="Lead"),
        ]


@pytest.fixture
def source_shift(event, location, roles):
    start = now() + timedelta(days=1)
    with scope(event=event):
        shift = Shift.objects.create(
            event=event,
            name="Setup",
            location=location,
            start_time=start,
            end_time=start + timedelta(hours=2),
            description="Set up the hall",
        )
        ShiftRoleAssignment.objects.create(shift=shift, role=roles[0], capacity=4)
        ShiftRoleAssignment.objects.create(shift=shift, role=roles[1], capacity=1)
        member = User.objects.create_user(email="volunteer@example.com", password="x")
        ShiftAssignment.objects.create(shift=shift, team_member=member, role=roles[0])
    return shift


def clone_url(event, shift):
    return reverse(
        "plugins:teamshifts:shift_clone",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )


@pytest.mark.django_db
def test_shift_clone_form_is_prefilled(orga_client, event, source_shift, location, roles):
    response = orga_client.get(clone_url(event, source_shift))
    assert response.status_code == 200
    form = response.context["form"]
    assert form.initial["name"] == "Setup"
    assert form.initial["location"] == location.pk
    assert form.initial["description"] == "Set up the hall"
    assert form.initial["start_time"] == source_shift.start_time
    assert form.initial["end_time"] == source_shift.end_time
    formset = response.context["formset"]
    assert [(f.initial["role"], f.initial["capacity"]) for f in formset.forms] == [(roles[0].pk, 4), (roles[1].pk, 1)]
    assert response.context["is_clone"] is True


@pytest.mark.django_db
def test_shift_clone_get_does_not_create_shift(orga_client, event, source_shift):
    orga_client.get(clone_url(event, source_shift))
    with scope(event=event):
        assert Shift.objects.count() == 1


@pytest.mark.django_db
def test_shift_clone_save_creates_independent_shift(orga_client, event, source_shift, location, roles):
    start = source_shift.start_time + timedelta(days=1)
    data = {
        "mode": "single",
        "name": "Setup",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        "roles-TOTAL_FORMS": "2",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": roles[0].pk,
        "roles-0-capacity": "4",
        "roles-1-role": roles[1].pk,
        "roles-1-capacity": "1",
    }
    response = orga_client.post(clone_url(event, source_shift), data)
    assert response.status_code == 302
    with scope(event=event):
        assert Shift.objects.count() == 2
        clone = Shift.objects.exclude(pk=source_shift.pk).get()
        assert clone.assignments.count() == 0
        assert clone.role_assignments.count() == 2
        assert source_shift.assignments.count() == 1
        assert source_shift.role_assignments.count() == 2
        clone.delete()
        assert Shift.objects.filter(pk=source_shift.pk).exists()
        assert source_shift.role_assignments.count() == 2


@pytest.mark.django_db
def test_shift_clone_from_other_event_is_404(orga_client, event, source_shift):
    with scope(event=event):
        other_event = Event.objects.create(
            organizer=event.organizer,
            name="Other Event",
            slug="other-event",
            date_from=now(),
            plugins="teamshifts",
        )
    url = reverse(
        "plugins:teamshifts:shift_clone",
        kwargs={"organizer": event.organizer.slug, "event": other_event.slug, "pk": source_shift.pk},
    )
    assert orga_client.get(url).status_code == 404


@pytest.mark.django_db
def test_shift_clone_post_with_missing_source_is_404(orga_client, event, source_shift, location, roles):
    url = reverse(
        "plugins:teamshifts:shift_clone",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": source_shift.pk + 999},
    )
    start = source_shift.start_time + timedelta(days=1)
    data = {
        "mode": "single",
        "name": "Setup",
        "location": location.pk,
        "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        "end_time": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        "roles-TOTAL_FORMS": "1",
        "roles-INITIAL_FORMS": "0",
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
        "roles-0-role": roles[0].pk,
        "roles-0-capacity": "1",
    }
    assert orga_client.post(url, data).status_code == 404
    with scope(event=event):
        assert Shift.objects.count() == 1


@pytest.mark.django_db
def test_shift_clone_requires_permission(client, event, source_shift, django_user_model, settings):
    settings.SITE_URL = "https://testserver"
    outsider = django_user_model.objects.create_user(email="outsider@example.com", password="x")
    client.force_login(outsider)
    assert client.get(clone_url(event, source_shift)).status_code == 404
    with scope(event=event):
        assert Shift.objects.count() == 1


@pytest.mark.django_db
def test_shift_list_shows_clone_button(orga_client, event, source_shift):
    url = reverse("plugins:teamshifts:shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = orga_client.get(url)
    assert clone_url(event, source_shift) in response.content.decode()

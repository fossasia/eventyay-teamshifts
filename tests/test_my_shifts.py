import pytest
from django.urls import reverse
from django.utils.timezone import now, timedelta
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, User

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    ShiftAssignment,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
)


@pytest.fixture
def user():
    return User.objects.create_user(email="volunteer@example.com", password="password")


@pytest.fixture
def organizer():
    return Organizer.objects.create(name="FOSSASIA", slug="fossasia")


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        name="FOSSASIA Summit",
        slug="fossasia-summit",
        organizer=organizer,
        date_from=now(),
        date_to=now() + timedelta(days=2),
        plugins="teamshifts",
    )


@pytest.fixture
def cfm(event):
    return CallForTeamMembers.objects.create(
        event=event,
        title="Join our team",
        active=True,
        shift_schedule_published=True,
    )


@pytest.fixture
def accepted_application(event, user):
    return TeamMemberApplication.objects.create(
        event=event,
        user=user,
        status=ApplicationStatus.ACCEPTED,
    )


@pytest.fixture
def team_role(event):
    return TeamRole.objects.create(event=event, name="Registration")


@pytest.fixture
def shift(event):
    return Shift.objects.create(
        event=event,
        name="Morning Shift",
        start_time=now(),
        end_time=now() + timedelta(hours=3),
    )


@pytest.fixture
def shift_assignment(shift, user, team_role):
    ShiftRoleAssignment.objects.create(shift=shift, role=team_role, capacity=5)
    return ShiftAssignment.objects.create(
        shift=shift,
        team_member=user,
        role=team_role,
    )


@pytest.fixture
def global_url():
    return reverse("plugins:teamshifts:my_shifts_global")


@pytest.mark.django_db
def test_my_shifts_unauthenticated(client, global_url):
    response = client.get(global_url)
    assert response.status_code == 302
    assert "/login" in response.url or "login" in response.url.lower()


@pytest.mark.django_db
def test_my_shifts_no_shifts(client, user, global_url):
    client.force_login(user)
    response = client.get(global_url)
    assert response.status_code == 404


@pytest.mark.django_db
def test_my_shifts_plugin_disabled(client, user, event, accepted_application, shift_assignment, global_url):
    event.plugins = ""
    event.save(update_fields=["plugins"])
    client.force_login(user)
    response = client.get(global_url)
    assert response.status_code == 404


@pytest.mark.django_db
def test_my_shifts_with_assignment(client, user, event, cfm, accepted_application, shift_assignment, global_url):
    client.force_login(user)
    with scopes_disabled():
        response = client.get(global_url)
    assert response.status_code == 200
    assert b"Registration" in response.content
    assert b"Morning Shift" in response.content


@pytest.mark.django_db
def test_old_event_my_shifts_redirects_to_global(client, event, global_url):
    old_url = reverse("plugins:teamshifts:my_shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.get(old_url)
    assert response.status_code == 302
    assert response.url == global_url


@pytest.mark.django_db
def test_old_event_my_shifts_preserves_query_string(client, event, global_url):
    old_url = reverse("plugins:teamshifts:my_shifts", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.get(f"{old_url}?search=morning")
    assert response.status_code == 302
    assert response.url == f"{global_url}?search=morning"


@pytest.mark.django_db
def test_old_checkout_url_redirects(client, event, shift_assignment):
    old_url = f"/{event.organizer.slug}/{event.slug}/teamshifts/my-shifts/{shift_assignment.pk}/checkout/"
    response = client.get(old_url)
    assert response.status_code == 302
    assert response.url == reverse("plugins:teamshifts:my_shifts_global")

    response_post = client.post(old_url)
    assert response_post.status_code == 307
    assert response_post.url == reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})


@pytest.mark.django_db
def test_my_shifts_with_active_assignment_renders_checkout_form(client, user, event, cfm, accepted_application, shift_assignment, global_url):
    shift_assignment.started_at = now() - timedelta(minutes=30)
    shift_assignment.save(update_fields=["started_at"])
    client.force_login(user)
    with scopes_disabled():
        response = client.get(global_url)
    assert response.status_code == 200
    checkout_url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    assert checkout_url.encode() in response.content
    assert b'class="shift-checkout-form"' in response.content


@pytest.mark.django_db
def test_shift_checkout_unauthenticated(client, shift_assignment):
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 302
    assert "/login" in response.url or "login" in response.url.lower()


@pytest.mark.django_db
def test_shift_checkout_success(client, user, shift_assignment):
    shift_assignment.started_at = now() - timedelta(minutes=30)
    shift_assignment.save(update_fields=["started_at"])
    client.force_login(user)
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "ended_at" in data
    shift_assignment.refresh_from_db()
    assert shift_assignment.ended_at is not None


@pytest.mark.django_db
def test_shift_checkout_not_started_yet(client, user, shift, shift_assignment):
    shift.start_time = now() + timedelta(hours=1)
    shift.end_time = now() + timedelta(hours=4)
    shift.save(update_fields=["start_time", "end_time"])
    shift_assignment.started_at = now()
    shift_assignment.save(update_fields=["started_at"])
    client.force_login(user)
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 400
    assert response.json()["status"] == "error"


@pytest.mark.django_db
def test_shift_checkout_not_checked_in(client, user, shift_assignment):
    client.force_login(user)
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 400
    assert response.json()["status"] == "error"


@pytest.mark.django_db
def test_shift_checkout_already_ended(client, user, shift_assignment):
    shift_assignment.started_at = now() - timedelta(hours=2)
    shift_assignment.ended_at = now() - timedelta(hours=1)
    shift_assignment.save(update_fields=["started_at", "ended_at"])
    client.force_login(user)
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 400
    assert response.json()["status"] == "error"


@pytest.mark.django_db
def test_shift_checkout_other_user(client, shift_assignment):
    other_user = User.objects.create_user(email="other@example.com", password="password")
    client.force_login(other_user)
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 404
    assert response.json()["status"] == "error"


@pytest.mark.django_db
def test_shift_checkout_plugin_disabled(client, user, event, shift_assignment):
    event.plugins = ""
    event.save(update_fields=["plugins"])
    shift_assignment.started_at = now() - timedelta(minutes=30)
    shift_assignment.save(update_fields=["started_at"])
    client.force_login(user)
    url = reverse("plugins:teamshifts:shift_checkout", kwargs={"pk": shift_assignment.pk})
    response = client.post(url)
    assert response.status_code == 404
    assert response.json()["status"] == "error"

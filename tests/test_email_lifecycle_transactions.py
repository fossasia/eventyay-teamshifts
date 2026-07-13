import pytest
from django.test import TestCase
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import Team

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
)


@pytest.fixture
def call_for_team_members(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(
            event=event,
            active=True,
        )


@pytest.fixture
def team_role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Volunteer")


@pytest.fixture
def applicant(django_user_model):
    return django_user_model.objects.create_user(email="applicant@example.com", password="x")


@pytest.fixture
def orga_user(event, user):
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Test Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    return user


@pytest.mark.django_db
def test_apply_view_queues_received_email(client, event, call_for_team_members, team_role, applicant, settings):
    settings.SITE_URL = "http://testserver"
    client.force_login(applicant)
    url = reverse("plugins:teamshifts:apply", kwargs={"organizer": event.organizer.slug, "event": event.slug})

    data = {
        "role": team_role.pk,
        "full_name": "Applicant Name",
        "email": applicant.email,
        "phone": "+123456789",
        "accept_terms": True,
    }
    tc = TestCase()
    with tc.captureOnCommitCallbacks(execute=True):
        response = client.post(url, data, HTTP_HOST="testserver")
    assert response.status_code in (200, 302)

    with scope(event=event):
        assert TeamMemberApplication.objects.filter(user=applicant).exists()
        assert TeamShiftsEmailQueue.objects.filter(event=event, role_filter=team_role).exists()


@pytest.fixture
def pending_application(event, team_role, applicant):
    with scope(event=event):
        return TeamMemberApplication.objects.create(
            event=event,
            user=applicant,
            role=team_role,
            status=ApplicationStatus.PENDING,
        )


@pytest.mark.django_db
def test_application_status_view_queues_accepted_email(client, event, team_role, pending_application, orga_user, settings):
    settings.SITE_URL = "http://testserver"
    client.force_login(orga_user)

    url = reverse(
        "plugins:teamshifts:application_status",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": pending_application.pk},
    )
    tc = TestCase()
    with tc.captureOnCommitCallbacks(execute=True):
        response = client.post(url, {"action": "accept"}, HTTP_HOST="testserver")

    expected_url = reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    assert response.status_code == 302
    assert expected_url in response.url

    with scope(event=event):
        pending_application.refresh_from_db()
        assert pending_application.status == ApplicationStatus.ACCEPTED
        assert TeamShiftsEmailQueue.objects.filter(event=event, role_filter=team_role).exists()


@pytest.mark.django_db
def test_application_status_view_queues_rejected_email(client, event, team_role, pending_application, orga_user, settings):
    settings.SITE_URL = "http://testserver"
    client.force_login(orga_user)

    url = reverse(
        "plugins:teamshifts:application_status",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": pending_application.pk},
    )
    tc = TestCase()
    with tc.captureOnCommitCallbacks(execute=True):
        response = client.post(url, {"action": "reject"}, HTTP_HOST="testserver")

    expected_url = reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    assert response.status_code == 302
    assert expected_url in response.url

    with scope(event=event):
        pending_application.refresh_from_db()
        assert pending_application.status == ApplicationStatus.REJECTED
        assert TeamShiftsEmailQueue.objects.filter(event=event, role_filter=team_role).exists()

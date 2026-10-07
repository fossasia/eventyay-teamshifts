import pytest
from django.urls import reverse
from django.utils import timezone
from django_scopes import scope

from teamshifts.models import CallForTeamMembers, TeamMemberApplication


@pytest.fixture
def call_for_team_members(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(
            event=event,
            active=True,
        )


@pytest.fixture
def applicant(django_user_model):
    return django_user_model.objects.create_user(
        email="applicant@example.com",
        password="x",
    )


@pytest.mark.django_db
def test_secret_link_allows_application_after_deadline(client, event, call_for_team_members, applicant):
    call_for_team_members.cfm_private = True
    call_for_team_members.deadline = timezone.now() - timezone.timedelta(hours=1)
    call_for_team_members.save(update_fields=["cfm_private", "deadline"])

    client.force_login(applicant)
    url = reverse(
        "plugins:teamshifts:apply_secret",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "secret": call_for_team_members.cfm_secret,
        },
    )

    response = client.get(url)

    assert response.status_code == 200
    assert b"Applications closed on" not in response.content


@pytest.mark.django_db
def test_secret_link_submits_application_after_deadline(client, event, call_for_team_members, applicant):
    call_for_team_members.cfm_private = True
    call_for_team_members.deadline = timezone.now() - timezone.timedelta(hours=1)
    call_for_team_members.save(update_fields=["cfm_private", "deadline"])

    client.force_login(applicant)
    url = reverse(
        "plugins:teamshifts:apply_secret",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "secret": call_for_team_members.cfm_secret,
        },
    )

    response = client.post(
        url,
        {
            "full_name": "Applicant Name",
            "email": applicant.email,
            "phone_0": "+1",
            "phone_1": "201 555 0123",
            "accept_terms": True,
        },
    )

    assert response.status_code == 302

    with scope(event=event):
        assert TeamMemberApplication.objects.filter(
            event=event,
            user=applicant,
        ).exists()

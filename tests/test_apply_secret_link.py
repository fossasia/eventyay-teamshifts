import pytest
from django.urls import reverse
from django.utils.timezone import now, timedelta
from django_scopes import scopes_disabled

from teamshifts.models import CallForTeamMembers, TeamMemberApplication


@pytest.fixture
def cfm(event):
    with scopes_disabled():
        return CallForTeamMembers.objects.create(
            event=event,
            title="Join our team",
            active=True,
            cfm_private=True,
            deadline=now() - timedelta(days=1),
        )


def _secret_url(event, secret):
    return reverse(
        "plugins:teamshifts:apply_secret",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "secret": secret},
    )


def _public_url(event):
    return reverse(
        "plugins:teamshifts:apply",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _application_data(user):
    return {"email": user.email, "full_name": "Jane Volunteer"}


@pytest.mark.django_db
def test_secret_link_shows_form_after_deadline(client, event, user, cfm):
    client.force_login(user)
    response = client.get(_secret_url(event, cfm.cfm_secret))
    assert response.status_code == 200
    assert response.context["cfm_open"] is True
    assert response.context["cfm_deadline_passed"] is False
    assert b"teamshifts-apply-form" in response.content


@pytest.mark.django_db
def test_secret_link_accepts_application_after_deadline(client, event, user, cfm):
    client.force_login(user)
    response = client.post(_secret_url(event, cfm.cfm_secret), _application_data(user))
    assert response.status_code == 302
    assert response.url == reverse(
        "plugins:teamshifts:apply_thanks",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    with scopes_disabled():
        assert TeamMemberApplication.objects.filter(event=event, user=user).exists()


@pytest.mark.django_db
def test_secret_link_rejects_application_when_call_inactive(client, event, user, cfm):
    with scopes_disabled():
        cfm.active = False
        cfm.save(update_fields=["active"])
    client.force_login(user)
    response = client.post(_secret_url(event, cfm.cfm_secret), _application_data(user))
    assert response.status_code == 404
    with scopes_disabled():
        assert not TeamMemberApplication.objects.filter(event=event, user=user).exists()


@pytest.mark.django_db
def test_invalid_secret_returns_404(client, event, user, cfm):
    client.force_login(user)
    response = client.get(_secret_url(event, "wrong-secret"))
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_link_still_enforces_deadline(client, event, user, cfm):
    with scopes_disabled():
        cfm.cfm_private = False
        cfm.save(update_fields=["cfm_private"])
    client.force_login(user)
    response = client.get(_public_url(event))
    assert response.status_code == 200
    assert response.context["cfm_open"] is False
    assert response.context["cfm_deadline_passed"] is True

    response = client.post(_public_url(event), _application_data(user))
    assert response.status_code == 200
    with scopes_disabled():
        assert not TeamMemberApplication.objects.filter(event=event, user=user).exists()

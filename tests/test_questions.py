import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Organizer, Team

from teamshifts.models import (
    CallForTeamMembers,
    TeamApplicationQuestion,
)


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
def question(event):
    with scope(event=event):
        return TeamApplicationQuestion.objects.create(
            event=event,
            question="T-Shirt Size",
            variant="string",
            required=False,
            active=True,
        )


@pytest.mark.django_db
def test_question_delete_get_confirmation_page(orga_client, event, question):
    url = reverse(
        "plugins:teamshifts:question_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": question.pk},
    )
    response = orga_client.get(url)
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Delete Custom Field" in content
    assert "custom field" in content
    assert "T-Shirt Size" in content
    assert "This action cannot be undone." in content
    form_url = reverse(
        "plugins:teamshifts:cfm_application_form",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    assert form_url in content
    assert "Cancel" in content
    assert "Delete" in content


@pytest.mark.django_db
def test_question_delete_post_success(orga_client, event, question):
    with scope(event=event):
        cfm = CallForTeamMembers.objects.create(
            event=event,
            field_order=["name", "email", question.pk],
        )

    url = reverse(
        "plugins:teamshifts:question_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": question.pk},
    )
    response = orga_client.post(url)
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "plugins:teamshifts:cfm_application_form",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    with scope(event=event):
        assert not TeamApplicationQuestion.objects.filter(pk=question.pk).exists()
        cfm.refresh_from_db()
        assert question.pk not in cfm.field_order


@pytest.mark.django_db
def test_question_delete_other_event_returns_404(orga_client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    other_organizer = Organizer.objects.create(name="Other Org", slug="other-org")
    other_event = Event.objects.create(
        organizer=other_organizer,
        name="Other Event",
        slug="other-event",
        live=True,
        date_from=now(),
        plugins="teamshifts",
    )
    with scope(event=other_event):
        other_question = TeamApplicationQuestion.objects.create(
            event=other_event,
            question="Other Question",
        )
    url = reverse(
        "plugins:teamshifts:question_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": other_question.pk},
    )
    response = orga_client.get(url)
    assert response.status_code == 404
    response = orga_client.post(url)
    assert response.status_code == 404


@pytest.mark.django_db
def test_cfm_application_form_renders_delete_link(orga_client, event, question):
    url = reverse(
        "plugins:teamshifts:cfm_application_form",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = orga_client.get(url)
    assert response.status_code == 200
    delete_url = reverse(
        "plugins:teamshifts:question_delete",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": question.pk},
    )
    content = response.content.decode("utf-8")
    assert f'href="{delete_url}"' in content
    # Should NOT be a submit button with formaction
    assert f'formaction="{delete_url}"' not in content

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Organizer, Team

from teamshifts.models import (
    CallForTeamMembers,
    TeamApplicationQuestion,
    TeamApplicationQuestionOption,
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


@pytest.mark.django_db
def test_question_edit_requires_at_least_two_options(orga_client, event):
    url = reverse(
        "plugins:teamshifts:question_create",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    data = {
        "question_0": "T-Shirt Size",
        "help_text": "",
        "variant": "choices",
        "required": "",
        "active": "on",
        "option_records-TOTAL_FORMS": "1",
        "option_records-INITIAL_FORMS": "0",
        "option_records-MIN_NUM_FORMS": "0",
        "option_records-MAX_NUM_FORMS": "1000",
        "option_records-0-answer": "Small",
        "option_records-0-ORDER": "0",
        "option_records-0-DELETE": "",
    }

    response = orga_client.post(url, data)

    assert response.status_code == 200
    assert "Please provide at least 2 options." in response.content.decode()


@pytest.mark.django_db
def test_question_edit_saves_two_options(orga_client, event):
    url = reverse(
        "plugins:teamshifts:question_create",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )

    data = {
        "question_0": "T-Shirt Size",
        "help_text": "",
        "variant": "choices",
        "required": "",
        "active": "on",
        "option_records-TOTAL_FORMS": "2",
        "option_records-INITIAL_FORMS": "0",
        "option_records-MIN_NUM_FORMS": "0",
        "option_records-MAX_NUM_FORMS": "1000",
        "option_records-0-answer_0": "Small",
        "option_records-0-ORDER": "0",
        "option_records-0-DELETE": "",
        "option_records-1-answer_0": "Large",
        "option_records-1-ORDER": "1",
        "option_records-1-DELETE": "",
    }

    response = orga_client.post(url, data)

    assert response.status_code == 302

    with scope(event=event):
        question = TeamApplicationQuestion.objects.latest("pk")
        options = list(TeamApplicationQuestionOption.objects.filter(question=question))

    assert [str(option.answer) for option in options] == ["Small", "Large"]
    assert [option.position for option in options] == [0, 1]


@pytest.mark.django_db
def test_question_edit_loads_existing_options(orga_client, event, question):
    with scope(event=event):
        question.variant = "choices"
        question.save(update_fields=["variant"])
        TeamApplicationQuestionOption.objects.create(
            question=question,
            answer="Small",
            position=0,
        )
        TeamApplicationQuestionOption.objects.create(
            question=question,
            answer="Large",
            position=1,
        )

    url = reverse(
        "plugins:teamshifts:question_edit",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "pk": question.pk,
        },
    )

    response = orga_client.get(url)

    assert response.status_code == 200

    option_forms = response.context["option_formset"].forms
    assert [str(form.instance.answer) for form in option_forms] == [
        "Small",
        "Large",
    ]


@pytest.mark.django_db
def test_question_edit_switches_to_non_choice_and_deletes_options(orga_client, event, question):
    with scope(event=event):
        question.variant = "choices"
        question.save(update_fields=["variant"])
        TeamApplicationQuestionOption.objects.create(
            question=question,
            answer="Small",
            position=0,
        )
        TeamApplicationQuestionOption.objects.create(
            question=question,
            answer="Large",
            position=1,
        )

    url = reverse(
        "plugins:teamshifts:question_edit",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "pk": question.pk,
        },
    )

    data = {
        "question_0": "T-Shirt Size",
        "help_text": "",
        "variant": "text",
        "required": "",
        "active": "on",
        "option_records-TOTAL_FORMS": "2",
        "option_records-INITIAL_FORMS": "2",
        "option_records-MIN_NUM_FORMS": "0",
        "option_records-MAX_NUM_FORMS": "1000",
        "option_records-0-answer_0": "Small",
        "option_records-0-ORDER": "0",
        "option_records-0-DELETE": "on",
        "option_records-1-answer_0": "Large",
        "option_records-1-ORDER": "1",
        "option_records-1-DELETE": "on",
    }

    response = orga_client.post(url, data)

    assert response.status_code == 302

    with scope(event=event):
        question.refresh_from_db()
        assert question.variant == "text"
        assert not TeamApplicationQuestionOption.objects.filter(question=question).exists()

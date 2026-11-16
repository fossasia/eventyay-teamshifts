from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Organizer, Team, User

from teamshifts.forms import EmailComposeForm, valid_user_pk, volunteer_choice_label
from teamshifts.models import ApplicationStatus, TeamMemberApplication, TeamShiftsEmailQueue, TeamShiftsEmailQueueRecipient
from teamshifts.services.email import get_recipients, queue_email


def _volunteer(event, email, status, fullname=""):
    volunteer = User.objects.create_user(email=email, password="x", fullname=fullname)
    with scope(event=event):
        TeamMemberApplication.objects.create(event=event, user=volunteer, status=status)
    return volunteer


@pytest.fixture
def accepted(event):
    return _volunteer(event, "alice@example.com", ApplicationStatus.ACCEPTED, "Alice Müller")


@pytest.fixture
def pending(event):
    return _volunteer(event, "chloe@example.com", ApplicationStatus.PENDING, "Chloe Nguyen")


@pytest.fixture
def rejected(event):
    return _volunteer(event, "ben@example.com", ApplicationStatus.REJECTED, "Ben Kowalski")


def _team_client(client, event, user, settings, **team_kwargs):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, all_events=True, **team_kwargs)
        team.members.add(user)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_client(client, event, user, settings):
    return _team_client(client, event, user, settings, name="Orga Team", can_change_event_settings=True)


@pytest.fixture
def hidden_email_lead_client(client, event, user, settings):
    return _team_client(client, event, user, settings, name="Leads", teamshifts_role="lead", hide_teamshifts_emails=True)


def _compose_url(event):
    return reverse("plugins:teamshifts:email_compose", kwargs={"organizer": event.organizer.slug, "event": event.slug})


def _select2_url(event):
    return reverse("plugins:teamshifts:compose_volunteers_select2", kwargs={"organizer": event.organizer.slug, "event": event.slug})


def _compose_data(**extra):
    return {"subject_0": "Hello", "message_0": "<p>Body</p>", "send_after": "", **extra}


def _queued(event):
    with scope(event=event):
        return list(TeamShiftsEmailQueue.objects.filter(event=event).prefetch_related("recipients"))


@pytest.mark.django_db
def test_get_recipients_with_user_ids_ignores_the_status(event, accepted, pending, rejected):
    users = get_recipients(event, status=ApplicationStatus.ACCEPTED, user_ids=[pending.pk, rejected.pk])

    assert {u.email for u in users} == {"chloe@example.com", "ben@example.com"}


@pytest.mark.django_db
def test_get_recipients_with_user_ids_never_reaches_outside_the_event(event, accepted):
    organizer = Organizer.objects.create(name="Other Org", slug="other-org")
    other_event = Event.objects.create(organizer=organizer, name="Other", slug="other", date_from=event.date_from, plugins="teamshifts")
    outsider = _volunteer(other_event, "outsider@example.com", ApplicationStatus.ACCEPTED, "Outsider")
    stranger = User.objects.create_user(email="stranger@example.com", password="x")

    users = get_recipients(event, user_ids=[accepted.pk, outsider.pk, stranger.pk])

    assert {u.email for u in users} == {"alice@example.com"}


@pytest.mark.django_db
def test_get_recipients_without_user_ids_still_filters_by_status(event, accepted, pending):
    assert {u.email for u in get_recipients(event, user_ids=[])} == {"alice@example.com"}
    assert {u.email for u in get_recipients(event, status="", user_ids=None)} == {"alice@example.com", "chloe@example.com"}


@pytest.mark.django_db
def test_volunteer_field_offers_every_applicant_but_nobody_else(event, accepted, pending, rejected):
    stranger = User.objects.create_user(email="stranger@example.com", password="x")

    field = EmailComposeForm(event=event).fields["individual_volunteers"]

    assert field.required is False
    assert set(field.queryset.values_list("pk", flat=True)) == {accepted.pk, pending.pk, rejected.pk}
    assert stranger.pk not in field.queryset.values_list("pk", flat=True)


@pytest.mark.django_db
def test_volunteer_field_rejects_users_without_an_application(event, accepted):
    stranger = User.objects.create_user(email="stranger@example.com", password="x")

    form = EmailComposeForm(
        data=_compose_data(individual_volunteers=[str(accepted.pk), str(stranger.pk)]),
        event=event,
    )

    assert not form.is_valid()
    assert "individual_volunteers" in form.errors


@pytest.mark.django_db
def test_volunteer_labels_only_show_the_email_when_allowed(event):
    named = User.objects.create_user(email="named@example.com", password="x", fullname="Named Person")
    unnamed = User.objects.create_user(email="unnamed@example.com", password="x")

    assert volunteer_choice_label(named, show_email=True) == "Named Person (named@example.com)"
    assert volunteer_choice_label(unnamed, show_email=True) == "unnamed@example.com"
    assert volunteer_choice_label(named, show_email=False) == "Named Person"
    hidden = volunteer_choice_label(unnamed, show_email=False)
    assert "unnamed@example.com" not in hidden
    assert str(unnamed.pk) in hidden


@pytest.mark.django_db
def test_select2_endpoint_searches_name_and_email_for_coordinators(coordinator_client, event, accepted, pending, rejected):
    by_name = coordinator_client.get(_select2_url(event), {"query": "alice"}).json()
    by_email = coordinator_client.get(_select2_url(event), {"query": "chloe@"}).json()

    assert by_name["results"] == [{"id": accepted.pk, "text": "Alice Müller (alice@example.com)"}]
    assert by_email["results"] == [{"id": pending.pk, "text": "Chloe Nguyen (chloe@example.com)"}]
    assert by_name["pagination"] == {"more": False}


@pytest.mark.django_db
def test_select2_endpoint_lists_every_applicant_regardless_of_status(coordinator_client, event, accepted, pending, rejected):
    data = coordinator_client.get(_select2_url(event)).json()

    assert {row["id"] for row in data["results"]} == {accepted.pk, pending.pk, rejected.pk}


@pytest.mark.django_db
def test_select2_endpoint_does_not_leak_emails_to_leads_who_cannot_see_them(hidden_email_lead_client, event, accepted):
    listed = hidden_email_lead_client.get(_select2_url(event)).json()
    probed = hidden_email_lead_client.get(_select2_url(event), {"query": "alice@example.com"}).json()

    assert listed["results"] == [{"id": accepted.pk, "text": "Alice Müller"}]
    assert probed["results"] == []


@pytest.mark.django_db
def test_select2_endpoint_paginates_without_a_phantom_last_page(coordinator_client, event):
    for number in range(40):
        _volunteer(event, f"v{number:02}@example.com", ApplicationStatus.ACCEPTED, f"Volunteer {number:02}")

    first = coordinator_client.get(_select2_url(event), {"page": 1}).json()
    second = coordinator_client.get(_select2_url(event), {"page": 2}).json()
    third = coordinator_client.get(_select2_url(event), {"page": 3}).json()

    assert (len(first["results"]), first["pagination"]["more"]) == (20, True)
    assert (len(second["results"]), second["pagination"]["more"]) == (20, False)
    assert third["results"] == []


@pytest.mark.django_db
def test_select2_endpoint_tolerates_a_bad_page_number(coordinator_client, event, accepted):
    assert coordinator_client.get(_select2_url(event), {"page": "abc"}).status_code == 200


@pytest.mark.django_db
def test_select2_endpoint_requires_a_team_member(client, event, user, accepted, settings):
    settings.SITE_URL = "https://testserver"
    client.force_login(user)

    assert client.get(_select2_url(event)).status_code in (302, 403, 404)


@pytest.mark.django_db
def test_compose_page_renders_the_volunteer_picker(coordinator_client, event):
    html = coordinator_client.get(_compose_url(event)).content.decode()

    assert "Send to specific people" in html
    assert 'data-model-select2="generic"' in html
    assert _select2_url(event) in html
    assert "data-compose-status" in html


@pytest.mark.django_db
def test_compose_sends_only_to_the_chosen_volunteers_and_ignores_the_status(coordinator_client, event, accepted, pending, rejected):
    with patch("teamshifts.services.email.send_queued_email"):
        response = coordinator_client.post(
            _compose_url(event),
            _compose_data(action="send", status=ApplicationStatus.ACCEPTED, individual_volunteers=[pending.pk, rejected.pk]),
        )

    assert response.status_code == 302
    (queue,) = _queued(event)
    assert {r.email for r in queue.recipients.all()} == {"chloe@example.com", "ben@example.com"}
    assert queue.status_filter == ""
    assert queue.individual_recipients is True


@pytest.mark.django_db
def test_compose_without_volunteers_still_uses_the_status_filter(coordinator_client, event, accepted, pending):
    with patch("teamshifts.services.email.send_queued_email"):
        coordinator_client.post(_compose_url(event), _compose_data(action="send", status=ApplicationStatus.ACCEPTED))

    (queue,) = _queued(event)
    assert {r.email for r in queue.recipients.all()} == {"alice@example.com"}
    assert queue.status_filter == ApplicationStatus.ACCEPTED
    assert queue.individual_recipients is False


@pytest.mark.django_db
def test_compose_goes_through_the_outbox_queue_for_chosen_volunteers(coordinator_client, event, accepted, pending):
    with patch("teamshifts.services.email.send_queued_email") as task:
        coordinator_client.post(_compose_url(event), _compose_data(action="outbox", individual_volunteers=[accepted.pk]))

    (queue,) = _queued(event)
    assert {r.email for r in queue.recipients.all()} == {"alice@example.com"}
    assert queue.is_draft is False
    assert queue.sent_at is None
    task.delay.assert_not_called()


@pytest.mark.django_db
def test_compose_rejects_a_user_who_never_applied(coordinator_client, event, accepted):
    stranger = User.objects.create_user(email="stranger@example.com", password="x")

    response = coordinator_client.post(_compose_url(event), _compose_data(action="send", individual_volunteers=[stranger.pk]))

    assert response.status_code == 200
    assert _queued(event) == []


@pytest.mark.django_db
def test_chosen_volunteers_stay_selected_when_the_form_has_errors(coordinator_client, event, accepted, pending):
    response = coordinator_client.post(
        _compose_url(event),
        {"action": "send", "individual_volunteers": [accepted.pk, pending.pk]},
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert f'<option value="{accepted.pk}" selected>' in html
    assert f'<option value="{pending.pk}" selected>' in html
    assert _queued(event) == []


@pytest.mark.django_db
def test_preview_lists_the_chosen_volunteers_and_keeps_them_selected(coordinator_client, event, accepted, pending, rejected):
    response = coordinator_client.post(
        _compose_url(event),
        _compose_data(action="preview", status=ApplicationStatus.ACCEPTED, individual_volunteers=[pending.pk]),
    )

    assert response.status_code == 200
    assert [u.pk for u in response.context["preview_recipients"]] == [pending.pk]
    html = response.content.decode()
    assert f'<option value="{pending.pk}" selected>' in html
    assert _queued(event) == []


@pytest.mark.django_db
def test_preview_ignores_ids_that_are_not_numbers_or_not_applicants(coordinator_client, event, accepted, pending):
    stranger = User.objects.create_user(email="stranger@example.com", password="x")

    response = coordinator_client.post(
        _compose_url(event),
        _compose_data(action="preview", individual_volunteers=["abc", "²", str(stranger.pk), str(accepted.pk)]),
    )

    assert response.status_code == 200
    assert [u.pk for u in response.context["preview_recipients"]] == [accepted.pk]


@pytest.mark.django_db
def test_hidden_email_leads_get_names_only_in_the_preselected_picker(hidden_email_lead_client, event, accepted):
    response = hidden_email_lead_client.post(
        _compose_url(event),
        _compose_data(action="preview", individual_volunteers=[accepted.pk]),
    )

    html = response.content.decode()
    assert "Alice Müller" in html
    assert "alice@example.com" not in html


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", 12), (7, 7), (" 7 ", 7), ("abc", None), ("1.5", None), ("", None), ("²", None), ("9" * 5000, None), (str(2**70), None)],
)
def test_valid_user_pk_only_accepts_numbers_the_database_can_hold(value, expected):
    assert valid_user_pk(value) == expected


UNUSABLE_IDS = ["9" * 5000, str(2**70), "abc", "1.5"]


@pytest.mark.django_db
def test_preview_survives_volunteer_ids_that_cannot_be_looked_up(coordinator_client, event, accepted, pending):
    response = coordinator_client.post(
        _compose_url(event),
        _compose_data(action="preview", individual_volunteers=[*UNUSABLE_IDS, str(accepted.pk)]),
    )

    assert response.status_code == 200
    assert [u.pk for u in response.context["preview_recipients"]] == [accepted.pk]


@pytest.mark.django_db
def test_preview_with_only_unusable_ids_does_not_fall_back_to_the_status_filter(coordinator_client, event, accepted, pending):
    response = coordinator_client.post(
        _compose_url(event),
        _compose_data(action="preview", status=ApplicationStatus.ACCEPTED, individual_volunteers=UNUSABLE_IDS),
    )

    assert response.status_code == 200
    assert list(response.context["preview_recipients"]) == []


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", UNUSABLE_IDS)
def test_send_reports_unusable_volunteer_ids_as_a_form_error(coordinator_client, event, accepted, bad_id):
    response = coordinator_client.post(_compose_url(event), _compose_data(action="send", individual_volunteers=[bad_id]))

    assert response.status_code == 200
    assert "individual_volunteers" in response.context["form"].errors
    assert _queued(event) == []


@pytest.mark.django_db
def test_select2_endpoint_handles_a_page_far_beyond_the_results(coordinator_client, event, accepted):
    for page in (10**30, 3):
        response = coordinator_client.get(_select2_url(event), {"page": page})

        assert response.status_code == 200
        assert response.json() == {"results": [], "pagination": {"more": False}}


@pytest.mark.django_db
def test_organizer_added_members_can_be_picked_like_any_other_volunteer(coordinator_client, event, accepted):
    added = _volunteer(event, "added@example.com", ApplicationStatus.ACCEPTED, "Added By Organizer")
    with scope(event=event):
        TeamMemberApplication.objects.filter(event=event, user=added).update(added_by_organizer=True)

    listed = {row["id"] for row in coordinator_client.get(_select2_url(event)).json()["results"]}
    with patch("teamshifts.services.email.send_queued_email"):
        coordinator_client.post(_compose_url(event), _compose_data(action="send", individual_volunteers=[added.pk]))

    assert added.pk in listed
    (queue,) = _queued(event)
    assert {r.email for r in queue.recipients.all()} == {"added@example.com"}
    # They already receive status-filtered sends, so picking them individually must work too.
    assert added.pk in {u.pk for u in get_recipients(event, status=ApplicationStatus.ACCEPTED)}


def _copy_page(client, event, queue):
    return client.get(_compose_url(event) + f"?copy={queue.pk}").content.decode()


@pytest.mark.django_db
def test_copying_an_email_sent_to_specific_volunteers_opens_on_accepted(coordinator_client, event, user, accepted):
    sent = queue_email(event, "Hello", "<p>Body</p>", [accepted], user=user, individual_recipients=True, dispatch=False)

    html = _copy_page(coordinator_client, event, sent)

    assert '<option value="accepted" selected>' in html
    assert '<option value="" selected>' not in html
    assert "Hello" in html
    assert f'<option value="{accepted.pk}" selected>' not in html


@pytest.mark.django_db
@pytest.mark.parametrize("status", [ApplicationStatus.PENDING, ApplicationStatus.REJECTED, ""])
def test_copying_other_emails_keeps_their_status_filter(coordinator_client, event, user, accepted, status):
    sent = queue_email(event, "Hello", "<p>Body</p>", [accepted], user=user, status_filter=status, dispatch=False)

    html = _copy_page(coordinator_client, event, sent)

    assert f'<option value="{status}" selected>' in html


@pytest.mark.django_db
def test_lists_label_emails_sent_to_specific_volunteers(coordinator_client, event, user, accepted):
    queue_email(event, "Picked mail", "<p>m</p>", [accepted], user=user, individual_recipients=True, dispatch=False)
    queue_email(event, "Filtered mail", "<p>m</p>", [accepted], user=user, status_filter=ApplicationStatus.ACCEPTED, dispatch=False)
    with scope(event=event):
        TeamShiftsEmailQueue.objects.filter(event=event).update(sent_at=now())
        TeamShiftsEmailQueueRecipient.objects.filter(queue__event=event).update(sent_at=now())

    html = coordinator_client.get(reverse("plugins:teamshifts:email_sent", kwargs={"organizer": event.organizer.slug, "event": event.slug})).content.decode()
    row_picked = html[html.index("Picked mail") :].split("</tr>")[0]
    row_filtered = html[html.index("Filtered mail") :].split("</tr>")[0]
    assert "Specific people" in row_picked
    assert "Specific people" not in row_filtered


@pytest.mark.django_db
def test_outbox_labels_emails_sent_to_specific_volunteers(coordinator_client, event, user, accepted):
    queue_email(event, "Picked mail", "<p>m</p>", [accepted], user=user, individual_recipients=True, dispatch=False)
    queue_email(event, "Filtered mail", "<p>m</p>", [accepted], user=user, status_filter=ApplicationStatus.ACCEPTED, dispatch=False)

    html = coordinator_client.get(reverse("plugins:teamshifts:email_outbox", kwargs={"organizer": event.organizer.slug, "event": event.slug})).content.decode()

    row_picked = html[html.index("Picked mail") :].split("</tr>")[0]
    row_filtered = html[html.index("Filtered mail") :].split("</tr>")[0]
    assert "Specific people" in row_picked
    assert "Specific people" not in row_filtered


def _save_draft(client, event, **data):
    with patch("teamshifts.services.email._dispatch") as dispatch:
        response = client.post(_compose_url(event), _compose_data(action="draft", **data))
    assert response.status_code == 302
    dispatch.assert_not_called()
    (draft,) = _queued(event)
    return draft


@pytest.mark.django_db
def test_saving_a_draft_with_chosen_volunteers_keeps_them_and_the_marker(coordinator_client, event, accepted, pending):
    draft = _save_draft(coordinator_client, event, status=ApplicationStatus.ACCEPTED, individual_volunteers=[pending.pk])

    assert draft.is_draft is True
    assert draft.individual_recipients is True
    assert {r.email for r in draft.recipients.all()} == {"chloe@example.com"}


@pytest.mark.django_db
def test_reopening_a_draft_with_chosen_volunteers_restores_them(coordinator_client, event, accepted, pending):
    draft = _save_draft(coordinator_client, event, status=ApplicationStatus.ACCEPTED, individual_volunteers=[pending.pk])

    html = coordinator_client.get(_compose_url(event) + f"?draft={draft.pk}").content.decode()

    assert "Edit draft" in html
    assert f'<option value="{pending.pk}" selected>' in html
    assert f'<option value="{accepted.pk}" selected>' not in html
    assert '<option value="accepted" selected>' in html
    assert '<option value="" selected>' not in html


@pytest.mark.django_db
def test_sending_a_reopened_draft_with_chosen_volunteers_reaches_only_them(coordinator_client, event, accepted, pending, rejected):
    draft = _save_draft(coordinator_client, event, individual_volunteers=[pending.pk])

    with patch("teamshifts.services.email._dispatch") as dispatch:
        response = coordinator_client.post(
            _compose_url(event),
            _compose_data(action="send", status="", draft_id=draft.pk, individual_volunteers=[pending.pk]),
        )

    assert response.status_code == 302
    dispatch.assert_called_once()
    (sent,) = _queued(event)
    assert sent.pk == draft.pk
    assert sent.is_draft is False
    assert sent.individual_recipients is True
    assert {r.email for r in sent.recipients.all()} == {"chloe@example.com"}


@pytest.mark.django_db
def test_clearing_the_picks_on_a_reopened_draft_switches_it_to_the_status_filter(coordinator_client, event, accepted, pending):
    draft = _save_draft(coordinator_client, event, individual_volunteers=[pending.pk])

    coordinator_client.post(_compose_url(event), _compose_data(action="draft", status=ApplicationStatus.ACCEPTED, draft_id=draft.pk))

    (updated,) = _queued(event)
    assert updated.individual_recipients is False
    assert updated.status_filter == ApplicationStatus.ACCEPTED
    assert {r.email for r in updated.recipients.all()} == {"alice@example.com"}


@pytest.mark.django_db
def test_reopening_a_draft_without_chosen_volunteers_keeps_its_status(coordinator_client, event, accepted, pending):
    draft = _save_draft(coordinator_client, event, status=ApplicationStatus.PENDING)

    html = coordinator_client.get(_compose_url(event) + f"?draft={draft.pk}").content.decode()

    assert draft.individual_recipients is False
    assert '<option value="pending" selected>' in html
    assert f'<option value="{pending.pk}" selected>' not in html


@pytest.mark.django_db
def test_drafts_list_labels_drafts_for_chosen_volunteers(coordinator_client, event, user, accepted):
    queue_email(event, "Picked draft", "<p>m</p>", [accepted], user=user, individual_recipients=True, is_draft=True)
    queue_email(event, "Everyone draft", "<p>m</p>", [accepted], user=user, is_draft=True)

    html = coordinator_client.get(reverse("plugins:teamshifts:email_drafts", kwargs={"organizer": event.organizer.slug, "event": event.slug})).content.decode()

    row_picked = html[html.index("Picked draft") :].split("</tr>")[0]
    row_everyone = html[html.index("Everyone draft") :].split("</tr>")[0]
    assert "Specific people" in row_picked
    assert "All statuses" not in row_picked
    assert "Specific people" not in row_everyone
    assert "All statuses" in row_everyone

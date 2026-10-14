import datetime
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team

from teamshifts.models import (
    ApplicationStatus,
    TeamMemberApplication,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from teamshifts.services.email import DraftNoLongerEditable, queue_email, update_queued_email
from teamshifts.signals import dispatch_scheduled_emails
from teamshifts.tasks import send_queued_email


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
def volunteer(event, django_user_model):
    member = django_user_model.objects.create_user(email="vol@example.com", password="x", fullname="Vol", locale="en")
    with scope(event=event):
        TeamMemberApplication.objects.create(event=event, user=member, status=ApplicationStatus.ACCEPTED)
    return member


def _url(event, name, **kwargs):
    return reverse(
        f"plugins:teamshifts:{name}",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, **kwargs},
    )


def _compose_data(action, subject="Hello team", message="See you there", **extra):
    return {
        "action": action,
        "status": ApplicationStatus.ACCEPTED,
        "subject_0": subject,
        "message_0": message,
        "send_after": "",
        **extra,
    }


@pytest.mark.django_db
def test_queue_email_draft_is_never_dispatched(event, volunteer):
    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        queue = queue_email(event, "s", "m", [volunteer], is_draft=True)

    mock_dispatch.assert_not_called()
    assert queue.is_draft is True


@pytest.mark.django_db
def test_send_task_skips_drafts(event, volunteer):
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.create(event=event, subject="s", message="m", locale="en", is_draft=True)
        TeamShiftsEmailQueueRecipient.objects.create(queue=queue, user=volunteer, email=volunteer.email)

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    mock_mail.assert_not_called()
    with scope(event=event):
        queue.refresh_from_db()
    assert queue.sent_at is None


@pytest.mark.django_db
def test_scheduled_sweep_ignores_drafts(event):
    cache.clear()
    past = now() - datetime.timedelta(minutes=5)
    with scope(event=event):
        due = TeamShiftsEmailQueue.objects.create(event=event, subject="due", message="m", locale="en", send_after=past)
        TeamShiftsEmailQueue.objects.create(event=event, subject="draft", message="m", locale="en", send_after=past, is_draft=True)

    with patch("teamshifts.signals.send_queued_email") as mock_task:
        dispatch_scheduled_emails(sender=None)

    assert {call.args[1] for call in mock_task.delay.call_args_list} == {due.pk}


@pytest.mark.django_db
def test_save_as_draft_lands_in_drafts_not_outbox(orga_client, event, user, volunteer):
    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        response = orga_client.post(_url(event, "email_compose"), _compose_data("draft"))

    assert response.status_code == 302
    assert response.url == _url(event, "email_drafts")
    mock_dispatch.assert_not_called()
    with scope(event=event):
        draft = TeamShiftsEmailQueue.objects.get(event=event)
    assert draft.is_draft is True
    assert draft.user == user

    drafts_page = orga_client.get(_url(event, "email_drafts")).content.decode()
    outbox_page = orga_client.get(_url(event, "email_outbox")).content.decode()
    assert "Hello team" in drafts_page
    assert "Hello team" not in outbox_page


@pytest.mark.django_db
def test_draft_can_be_saved_half_written(orga_client, event, volunteer):
    response = orga_client.post(_url(event, "email_compose"), _compose_data("draft", subject="", message=""))

    assert response.status_code == 302
    with scope(event=event):
        assert TeamShiftsEmailQueue.objects.get(event=event).is_draft is True


@pytest.mark.django_db
def test_reopening_draft_restores_its_state(orga_client, event, user, volunteer):
    with scope(event=event):
        draft = queue_email(
            event,
            "Draft subject",
            "Draft body",
            [volunteer],
            user=user,
            status_filter=ApplicationStatus.PENDING,
            is_draft=True,
        )

    content = orga_client.get(_url(event, "email_compose") + f"?draft={draft.pk}").content.decode()

    assert "Edit draft" in content
    assert "Draft subject" in content
    assert "Draft body" in content
    assert f"name='draft_id' value='{draft.pk}'" in content
    assert f'value="{ApplicationStatus.PENDING}" selected' in content


@pytest.mark.django_db
def test_moving_draft_to_outbox_reuses_row_and_rebuilds_recipients(orga_client, event, user, volunteer):
    with scope(event=event):
        draft = queue_email(event, "Old", "Old body", [], user=user, is_draft=True)

    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        response = orga_client.post(_url(event, "email_compose"), _compose_data("outbox", draft_id=draft.pk))

    assert response.status_code == 302
    assert response.url == _url(event, "email_outbox")
    mock_dispatch.assert_not_called()
    with scope(event=event):
        assert TeamShiftsEmailQueue.objects.filter(event=event).count() == 1
        draft.refresh_from_db()
        emails = list(draft.recipients.values_list("email", flat=True))
    assert draft.is_draft is False
    assert str(draft.subject) == "Hello team"
    assert emails == [volunteer.email]


@pytest.mark.django_db
def test_sending_a_draft_dispatches_it(orga_client, event, user, volunteer):
    with scope(event=event):
        draft = queue_email(event, "s", "m", [volunteer], user=user, is_draft=True)

    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        orga_client.post(_url(event, "email_compose"), _compose_data("send", draft_id=draft.pk))

    mock_dispatch.assert_called_once_with(event.pk, draft.pk, eta=None)
    with scope(event=event):
        draft.refresh_from_db()
    assert draft.is_draft is False


@pytest.mark.django_db
def test_send_now_refuses_drafts(orga_client, event, user, volunteer):
    with scope(event=event):
        draft = queue_email(event, "s", "m", [volunteer], user=user, is_draft=True)

    with patch("teamshifts.views.send_queued_email") as mock_task:
        response = orga_client.post(_url(event, "email_send_now", pk=draft.pk))

    assert response.url == _url(event, "email_drafts")
    mock_task.delay.assert_not_called()
    with scope(event=event):
        draft.refresh_from_db()
    assert draft.is_draft is True


@pytest.mark.django_db
def test_outbox_edit_link_redirects_drafts_to_compose(orga_client, event, user, volunteer):
    with scope(event=event):
        draft = queue_email(event, "s", "m", [volunteer], user=user, is_draft=True)

    response = orga_client.get(_url(event, "email_edit", pk=draft.pk))

    assert response.status_code == 302
    assert response.url == _url(event, "email_compose") + f"?draft={draft.pk}"


@pytest.mark.django_db
def test_send_to_outbox_still_parks_mail_without_dispatching(orga_client, event, volunteer):
    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        response = orga_client.post(_url(event, "email_compose"), _compose_data("outbox"))

    assert response.url == _url(event, "email_outbox")
    mock_dispatch.assert_not_called()
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.get(event=event)
    assert queue.is_draft is False
    assert queue.sent_at is None


@pytest.mark.django_db
def test_stale_draft_submission_does_not_create_a_new_email(orga_client, event, user, volunteer):
    with scope(event=event):
        draft = queue_email(event, "s", "m", [volunteer], user=user, is_draft=True)
        draft_pk = draft.pk
        draft.delete()

    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        response = orga_client.post(_url(event, "email_compose"), _compose_data("send", draft_id=draft_pk))

    assert response.url == _url(event, "email_drafts")
    mock_dispatch.assert_not_called()
    with scope(event=event):
        assert not TeamShiftsEmailQueue.objects.filter(event=event).exists()


@pytest.mark.django_db
def test_update_refuses_a_draft_already_moved_out(event, user, volunteer):
    with scope(event=event):
        draft = queue_email(event, "s", "m", [volunteer], user=user, is_draft=True)
        stale = TeamShiftsEmailQueue.objects.get(pk=draft.pk)
    with patch("teamshifts.services.email._dispatch"):
        update_queued_email(draft, subject="s", message="m", recipients=[volunteer], is_draft=False)

    with pytest.raises(DraftNoLongerEditable):
        update_queued_email(stale, subject="s", message="m", recipients=[volunteer], is_draft=True)

    with scope(event=event):
        draft.refresh_from_db()
    assert draft.is_draft is False


@pytest.mark.django_db
def test_failed_recipient_rebuild_leaves_the_draft_untouched(event, user, volunteer, django_user_model):
    other = django_user_model.objects.create_user(email="other@example.com", password="x")
    with scope(event=event):
        draft = queue_email(event, "s", "m", [volunteer], user=user, is_draft=True)

    with (
        patch("teamshifts.services.email._dispatch") as mock_dispatch,
        patch.object(TeamShiftsEmailQueueRecipient.objects, "bulk_create", side_effect=RuntimeError("db down")),
        pytest.raises(RuntimeError),
    ):
        update_queued_email(draft, subject="new", message="m", recipients=[other], is_draft=False)

    mock_dispatch.assert_not_called()
    with scope(event=event):
        draft.refresh_from_db()
        emails = list(draft.recipients.values_list("email", flat=True))
    assert draft.is_draft is True
    assert str(draft.subject) == "s"
    assert emails == [volunteer.email]

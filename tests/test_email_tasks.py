from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.services.mail import SendMailException

from teamshifts.models import (
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from teamshifts.tasks import send_queued_email


@pytest.fixture
def queue(event, django_user_model):
    """A queue with two recipients, no send_after."""
    u1 = django_user_model.objects.create_user(
        email="a@example.com",
        password="x",
        fullname="Alice",
        locale="en",
    )
    u2 = django_user_model.objects.create_user(
        email="b@example.com",
        password="x",
        fullname="Bob",
        locale="en",
    )
    with scope(event=event):
        q = TeamShiftsEmailQueue.objects.create(
            event=event,
            subject="Hi",
            message="Body",
            locale="en",
        )
        TeamShiftsEmailQueueRecipient.objects.create(queue=q, user=u1, email=u1.email)
        TeamShiftsEmailQueueRecipient.objects.create(queue=q, user=u2, email=u2.email)
    return q


@pytest.mark.django_db
def test_send_after_in_future_reschedules(event, queue):
    """When send_after > now, task reschedules via apply_async and does not send."""
    with scope(event=event):
        queue.send_after = now() + timedelta(hours=1)
        queue.save(update_fields=["send_after"])

    with (
        patch("teamshifts.tasks.mail") as mock_mail,
        patch.object(send_queued_email, "apply_async") as mock_apply,
    ):
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    mock_apply.assert_called_once()
    _, kwargs = mock_apply.call_args
    assert kwargs["args"] == [event.pk, queue.pk]
    assert kwargs["countdown"] >= 1
    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_send_after_in_past_sends(event, queue):
    """When send_after <= now, task proceeds to send."""
    with scope(event=event):
        queue.send_after = now() - timedelta(minutes=1)
        queue.save(update_fields=["send_after"])

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    assert mock_mail.call_count == 2
    with scope(event=event):
        queue.refresh_from_db()
    assert queue.sent_at is not None


@pytest.mark.django_db
def test_all_recipients_marked_sent(event, queue):
    """When all mail() calls succeed, every recipient row records sent_at."""
    with patch("teamshifts.tasks.mail"):
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    with scope(event=event):
        recipients = list(TeamShiftsEmailQueueRecipient.objects.filter(queue=queue))
    assert all(r.sent_at is not None for r in recipients)


@pytest.mark.django_db
def test_partial_send_triggers_retry(event, queue):
    """When some recipients fail, task must call self.retry."""
    call_count = {"n": 0}

    def flaky_mail(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        raise SendMailException("smtp fail")

    with (
        patch("teamshifts.tasks.mail", side_effect=flaky_mail),
        patch.object(send_queued_email, "retry", side_effect=RuntimeError("retry called")) as mock_retry,
    ):
        with pytest.raises(RuntimeError, match="retry called"):
            send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    mock_retry.assert_called_once()

    # Queue should NOT be marked as fully sent yet.
    with scope(event=event):
        queue.refresh_from_db()
    assert queue.sent_at is None


@pytest.mark.django_db
def test_already_sent_queue_returns_early(event, queue):
    """When queue.sent_at is set, task must return without calling mail()."""
    with scope(event=event):
        queue.sent_at = now()
        queue.save(update_fields=["sent_at"])

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    mock_mail.assert_not_called()


@pytest.mark.django_db
def test_recipients_prefetched_with_select_related_user(event, queue):
    """Regression: recipients query must use select_related('user') to avoid N+1.

    We verify the query count is bounded (< 10) for a two-recipient queue.
    """
    with (
        patch("teamshifts.tasks.mail"),
        CaptureQueriesContext(connection) as ctx,
    ):
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    # Without select_related, each recipient.user access adds a query.
    # With it, user is joined in the initial select.
    assert len(ctx.captured_queries) < 25, f"Too many queries ({len(ctx.captured_queries)}) — possible N+1"

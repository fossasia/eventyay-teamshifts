import datetime
from unittest.mock import patch

import pytest
from django.utils.timezone import now
from django_scopes import scope

from teamshifts.models import (
    ApplicationStatus,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from teamshifts.services.email import get_recipients, queue_email


@pytest.fixture
def role(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Volunteers")


@pytest.fixture
def accepted_user(event, role, django_user_model):
    u = django_user_model.objects.create_user(
        email="accepted@example.com",
        password="x",
        fullname="Accepted",
        locale="en",
    )
    with scope(event=event):
        TeamMemberApplication.objects.create(
            event=event,
            user=u,
            role=role,
            status=ApplicationStatus.ACCEPTED,
        )
    return u


@pytest.fixture
def pending_user(event, role, django_user_model):
    u = django_user_model.objects.create_user(
        email="pending@example.com",
        password="x",
        fullname="Pending",
        locale="en",
    )
    with scope(event=event):
        TeamMemberApplication.objects.create(
            event=event,
            user=u,
            role=role,
            status=ApplicationStatus.PENDING,
        )
    return u


@pytest.mark.django_db
def test_get_recipients_returns_accepted_only(event, role, accepted_user, pending_user):
    """Default status filter returns only accepted applicants."""
    users = get_recipients(event=event, role=role)
    emails = {u.email for u in users}
    assert emails == {"accepted@example.com"}


@pytest.mark.django_db
def test_get_recipients_status_filter(event, role, accepted_user, pending_user):
    """Explicit status filter is honoured."""
    users = get_recipients(event=event, role=role, status=ApplicationStatus.PENDING)
    emails = {u.email for u in users}
    assert emails == {"pending@example.com"}


@pytest.mark.django_db
def test_get_recipients_no_scope_error(event, role, accepted_user):
    """Regression test: user_ids must be evaluated inside scope() so
    User.objects.filter(pk__in=...) does not trigger a ScopeError."""
    # If user_ids were a lazy QuerySet, this would raise ScopeError.
    users = get_recipients(event=event, role=role)
    assert len(users) == 1


@pytest.mark.django_db
def test_queue_email_creates_queue_and_recipients(event, accepted_user):
    """queue_email creates a TeamShiftsEmailQueue with deduped recipients."""
    queue = queue_email(
        event=event,
        subject="Hi",
        message="Hello {full_name}",
        recipients=[accepted_user],
    )
    with scope(event=event):
        assert TeamShiftsEmailQueue.objects.filter(pk=queue.pk).exists()
        recipients = list(TeamShiftsEmailQueueRecipient.objects.filter(queue=queue))
    assert len(recipients) == 1
    assert recipients[0].email == "accepted@example.com"


@pytest.mark.django_db
def test_queue_email_deduplicates_recipients(event, django_user_model):
    """queue_email must collapse duplicate email addresses.

    Same user passed twice, plus a synthetic user with a case-different email
    that shares the normalised form of the first user's email — all three
    should collapse to a single recipient row.
    """
    u1 = django_user_model.objects.create_user(
        email="dup@example.com",
        password="x",
        fullname="One",
        locale="en",
    )

    # Synthetic in-memory user with case-different but equal-when-normalised email.
    class _FakeUser:
        pk = 999
        email = "DUP@Example.com"

    queue = queue_email(event=event, subject="s", message="m", recipients=[u1, u1, _FakeUser()])
    with scope(event=event):
        recipients = list(TeamShiftsEmailQueueRecipient.objects.filter(queue=queue))
    assert len(recipients) == 1
    assert recipients[0].email == "dup@example.com"


@pytest.mark.django_db
def test_queue_email_send_after_persists(event, accepted_user):
    """send_after argument must be stored on the queue row."""
    when = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
    queue = queue_email(
        event=event,
        subject="s",
        message="m",
        recipients=[accepted_user],
        send_after=when,
    )
    with scope(event=event):
        queue.refresh_from_db()
    assert queue.send_after == when


@pytest.mark.django_db
def test_dispatch_uses_apply_async_when_eta_given(event, accepted_user):
    """When send_after is provided, _dispatch must call apply_async with eta."""
    when = datetime.datetime(2099, 6, 1, tzinfo=datetime.UTC)
    with (
        patch("teamshifts.services.email.send_queued_email") as mock_task,
        patch("teamshifts.services.email.transaction.on_commit", side_effect=lambda fn: fn()),
    ):
        queue = queue_email(
            event=event,
            subject="s",
            message="m",
            recipients=[accepted_user],
            send_after=when,
        )
        mock_task.apply_async.assert_called_once()
        _, kwargs = mock_task.apply_async.call_args
        assert kwargs["eta"] == when
        assert queue.pk in kwargs["args"]
        mock_task.delay.assert_not_called()


@pytest.mark.django_db
def test_dispatch_uses_delay_when_no_eta(event, accepted_user):
    """When send_after is None, _dispatch must call .delay() (immediate dispatch)."""
    with (
        patch("teamshifts.services.email.send_queued_email") as mock_task,
        patch("teamshifts.services.email.transaction.on_commit", side_effect=lambda fn: fn()),
    ):
        queue_email(
            event=event,
            subject="s",
            message="m",
            recipients=[accepted_user],
            send_after=None,
        )
        mock_task.delay.assert_called_once()
        mock_task.apply_async.assert_not_called()


@pytest.mark.django_db
def test_dispatch_scheduled_emails_enqueues_due_queues(event):
    """Periodic handler must call send_queued_email.delay() for each due queue."""
    from teamshifts.signals import dispatch_scheduled_emails

    past = now() - datetime.timedelta(minutes=5)
    with scope(event=event):
        q1 = TeamShiftsEmailQueue.objects.create(event=event, subject="s1", message="m", locale="en", send_after=past)
        q2 = TeamShiftsEmailQueue.objects.create(event=event, subject="s2", message="m", locale="en", send_after=past)
        TeamShiftsEmailQueue.objects.create(
            event=event,
            subject="future",
            message="m",
            locale="en",
            send_after=now() + datetime.timedelta(hours=1),
        )
        TeamShiftsEmailQueue.objects.create(
            event=event,
            subject="done",
            message="m",
            locale="en",
            send_after=past,
            sent_at=now(),
        )

    with patch("teamshifts.signals.send_queued_email") as mock_task:
        dispatch_scheduled_emails(sender=None)

    enqueued_queue_ids = {call.args[1] for call in mock_task.delay.call_args_list}
    assert enqueued_queue_ids == {q1.pk, q2.pk}

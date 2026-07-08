from datetime import UTC, datetime

import pytest
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
    when = datetime(2099, 1, 1, tzinfo=UTC)
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

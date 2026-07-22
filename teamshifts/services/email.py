import logging
from collections.abc import Iterable

from django.db import transaction
from django_scopes import scope
from eventyay.base.models import Event, User

from ..models import (
    ApplicationStatus,
    CallForTeamMembers,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from ..tasks import send_queued_email

logger = logging.getLogger(__name__)


def get_recipients(
    event: Event,
    *,
    status: str = ApplicationStatus.ACCEPTED,
) -> list[User]:
    with scope(event=event):
        qs = TeamMemberApplication.objects.filter(event=event)
        if status:
            qs = qs.filter(status=status)
        user_ids = list(qs.values_list("user_id", flat=True).distinct())
    return list(User.objects.filter(pk__in=user_ids))


def queue_email(
    event: Event,
    subject,
    message,
    recipients: Iterable[User],
    *,
    user: User | None = None,
    reply_to: str = "",
    bcc: str = "",
    locale: str = "",
    role_filter: TeamRole | None = None,
    status_filter: str = "",
    send_after=None,
    dispatch: bool = True,
) -> TeamShiftsEmailQueue:
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=user,
            subject=subject,
            message=message,
            reply_to=reply_to,
            bcc=bcc,
            locale=locale or event.settings.locale,
            role_filter=role_filter,
            status_filter=status_filter or "",
            send_after=send_after,
        )
        seen: set[str] = set()
        rows: list[TeamShiftsEmailQueueRecipient] = []
        for u in recipients:
            email = (u.email or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            rows.append(TeamShiftsEmailQueueRecipient(queue=queue, user=u, email=email))
        if rows:
            TeamShiftsEmailQueueRecipient.objects.bulk_create(rows)

    if dispatch:
        _dispatch(event.pk, queue.pk, eta=send_after)
    return queue


def _dispatch(event_id: int, queue_id: int, eta=None) -> None:
    if eta is not None:
        transaction.on_commit(lambda: send_queued_email.apply_async(args=[event_id, queue_id], eta=eta))
    else:
        transaction.on_commit(lambda: send_queued_email.delay(event_id, queue_id))


def queue_lifecycle_email(application, role: str) -> TeamShiftsEmailQueue | None:
    if not application.user or not application.user.email:
        logger.warning("[TeamShifts] Skipping %s email: user has no email", role)
        return None

    event = application.event

    try:
        cfm = event.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        logger.warning("[TeamShifts] No CFM found for event %s, skipping %s email", event.slug, role)
        return None

    template = cfm.get_mail_template(role)

    return queue_email(
        event=event,
        subject=template.subject,
        message=template.body,
        recipients=[application.user],
        status_filter=application.status,
    )

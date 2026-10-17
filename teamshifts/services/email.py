import logging
from collections.abc import Iterable
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, User

from ..models import (
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    Shift,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from ..tasks import send_queued_email

logger = logging.getLogger(__name__)

SHIFT_SUMMARY_SEND_TIME = time(20, 0)


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
    shift: Shift | None = None,
    shift_role: TeamRole | None = None,
    status_filter: str = "",
    send_after=None,
    dispatch: bool = True,
    is_shift_summary: bool = False,
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
            shift=shift,
            shift_role=shift_role,
            status_filter=status_filter or "",
            send_after=send_after,
            is_shift_summary=is_shift_summary,
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
        return

    def _send():
        try:
            send_queued_email.delay(event_id, queue_id)
        except Exception:
            logger.exception("[TeamShifts] Failed to dispatch queue %s to Celery; falling back to scheduled retry", queue_id)
            with scope(event=event_id):
                TeamShiftsEmailQueue.objects.filter(pk=queue_id, sent_at__isnull=True, send_after__isnull=True).update(send_after=now())

    transaction.on_commit(_send)


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


def queue_shift_notification_email(
    event: Event,
    user: User,
    shift: Shift,
    role: TeamRole | None,
    template_role: str,
) -> TeamShiftsEmailQueue | None:
    if not user.email:
        logger.warning("[TeamShifts] Skipping %s email: user has no email", template_role)
        return None

    try:
        cfm = event.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        logger.warning("[TeamShifts] No CFM found for event %s, skipping %s email", event.slug, template_role)
        return None

    if not cfm.shift_schedule_published:
        logger.info(
            "[TeamShifts] Skipping %s email: shift schedule not published for event %s",
            template_role,
            event.slug,
        )
        return None

    template = cfm.get_mail_template(template_role)

    return queue_email(
        event=event,
        subject=template.subject,
        message=template.body,
        recipients=[user],
        shift=shift,
        shift_role=role,
    )


def next_shift_summary_time(event: Event, current: datetime) -> datetime:
    local = current.astimezone(event.tz)
    target = datetime.combine(local.date(), SHIFT_SUMMARY_SEND_TIME, tzinfo=event.tz)
    if target <= local:
        target = datetime.combine(local.date() + timedelta(days=1), SHIFT_SUMMARY_SEND_TIME, tzinfo=event.tz)
    return target


def queue_shift_summary_email(event: Event, user: User) -> TeamShiftsEmailQueue | None:
    """One summary per volunteer per day; the shift list is rendered when it is sent."""
    if not user.email:
        logger.warning("[TeamShifts] Skipping shift summary email: user has no email")
        return None

    try:
        cfm = event.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        logger.warning("[TeamShifts] No CFM found for event %s, skipping shift summary email", event.slug)
        return None

    if not cfm.shift_schedule_published:
        logger.info("[TeamShifts] Skipping shift summary email: shift schedule not published for event %s", event.slug)
        return None

    current = now()
    with scope(event=event), transaction.atomic():
        # Serialize concurrent changes for one volunteer: there is no pending row to lock yet.
        # An unsent summary is reused even when overdue, as long as the sender has not claimed the recipient: the
        # list is built after the claim, so it already includes this change.
        User.objects.select_for_update().get(pk=user.pk)
        pending = (
            TeamShiftsEmailQueue.objects.select_for_update()
            .filter(
                event=event,
                is_shift_summary=True,
                sent_at__isnull=True,
                recipients__user=user,
                recipients__sent_at__isnull=True,
            )
            .first()
        )
        if pending is not None:
            return pending
        template = cfm.get_mail_template(EmailTemplateRoles.SHIFT_SUMMARY)
        return queue_email(
            event=event,
            subject=template.subject,
            message=template.body,
            recipients=[user],
            send_after=next_shift_summary_time(event, current),
            dispatch=False,
            is_shift_summary=True,
        )

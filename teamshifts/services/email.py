import logging
from collections.abc import Iterable
from datetime import timedelta

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

SHIFT_SUMMARY_DELAY = timedelta(minutes=30)
SHIFT_SUMMARY_IMMEDIATE_WINDOW = timedelta(hours=2)


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


def queue_shift_summary_email(
    event: Event,
    user: User,
    *,
    shift: Shift | None = None,
    signed_up: bool = False,
) -> TeamShiftsEmailQueue | None:
    """Queue one debounced summary of the volunteer's shifts.

    Every sign-up or drop pushes the pending summary back by SHIFT_SUMMARY_DELAY.
    A sign-up for a shift starting within SHIFT_SUMMARY_IMMEDIATE_WINDOW sends it right away.
    The shift list is rendered when the email is sent, so it reflects the final state.
    """
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
    immediate = bool(signed_up and shift is not None and shift.start_time <= current + SHIFT_SUMMARY_IMMEDIATE_WINDOW)
    send_after = None if immediate else current + SHIFT_SUMMARY_DELAY

    with scope(event=event), transaction.atomic():
        pending = (
            TeamShiftsEmailQueue.objects.select_for_update()
            .filter(
                event=event,
                is_shift_summary=True,
                sent_at__isnull=True,
                send_after__gt=current,
                recipients__user=user,
            )
            .first()
        )
        if pending is not None:
            pending.send_after = current if immediate else send_after
            pending.save(update_fields=["send_after", "updated"])
            queue = pending
        else:
            template = cfm.get_mail_template(EmailTemplateRoles.SHIFT_SUMMARY)
            queue = queue_email(
                event=event,
                subject=template.subject,
                message=template.body,
                recipients=[user],
                send_after=send_after,
                dispatch=False,
                is_shift_summary=True,
            )

    if immediate:
        _dispatch(event.pk, queue.pk)
    return queue

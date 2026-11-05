import logging
from collections.abc import Iterable

from django.db import transaction
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, User

from ..models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)
from ..tasks import render_subject, send_queued_email

logger = logging.getLogger(__name__)


class DraftNoLongerEditable(Exception):
    pass


def get_recipients(
    event: Event,
    *,
    status: str = ApplicationStatus.ACCEPTED,
    user_ids: Iterable[int] | None = None,
) -> list[User]:
    user_ids = list(user_ids or ())
    with scope(event=event):
        qs = TeamMemberApplication.objects.filter(event=event)
        if user_ids:
            qs = qs.filter(user_id__in=user_ids)
        elif status:
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
    individual_recipients: bool = False,
    mail_type: str = "",
    send_after=None,
    dispatch: bool = True,
    is_draft: bool = False,
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
            individual_recipients=individual_recipients,
            mail_type=mail_type,
            send_after=send_after,
            is_draft=is_draft,
        )
        _set_recipients(queue, recipients)

    if dispatch and not is_draft:
        _dispatch(event.pk, queue.pk, eta=send_after)
    return queue


def record_sent_email(
    event: Event,
    user: User,
    subject,
    message,
    context: dict,
    locale: str,
    mail_type: str,
) -> TeamShiftsEmailQueue:
    sent_now = now()
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=user,
            subject=subject,
            message=message,
            locale=locale or "",
            mail_type=mail_type,
            sent_at=sent_now,
        )
        TeamShiftsEmailQueueRecipient.objects.create(
            queue=queue,
            user=user,
            email=user.email,
            subject=render_subject(subject, context, locale or event.settings.locale, event),
            sent_at=sent_now,
        )
    return queue


def update_queued_email(
    queue: TeamShiftsEmailQueue,
    *,
    subject,
    message,
    recipients: Iterable[User],
    status_filter: str = "",
    individual_recipients: bool = False,
    send_after=None,
    is_draft: bool = False,
    dispatch: bool = True,
) -> TeamShiftsEmailQueue:
    with scope(event=queue.event), transaction.atomic():
        locked = TeamShiftsEmailQueue.objects.select_for_update().filter(pk=queue.pk, is_draft=True, sent_at__isnull=True).first()
        if locked is None:
            raise DraftNoLongerEditable(queue.pk)
        locked.subject = subject
        locked.message = message
        locked.status_filter = status_filter or ""
        locked.individual_recipients = individual_recipients
        locked.send_after = send_after
        locked.is_draft = is_draft
        locked.save(update_fields=["subject", "message", "status_filter", "individual_recipients", "send_after", "is_draft", "updated"])
        locked.recipients.all().delete()
        _set_recipients(locked, recipients)

    if dispatch and not is_draft:
        _dispatch(locked.event_id, locked.pk, eta=send_after)
    return locked


def _set_recipients(queue: TeamShiftsEmailQueue, recipients: Iterable[User]) -> None:
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
        mail_type=role,
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
        mail_type=template_role,
    )

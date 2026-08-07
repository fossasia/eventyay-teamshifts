import logging

from celery.exceptions import MaxRetriesExceededError
from django.core.cache import cache
from django.db import transaction
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled
from eventyay.base.email import get_email_context
from eventyay.base.models import Event
from eventyay.base.services.mail import SendMailException, mail
from eventyay.base.services.tasks import ProfiledEventTask
from eventyay.celery_app import app
from i18nfield.strings import LazyI18nString

from .models import TeamShiftsEmailQueue

logger = logging.getLogger(__name__)

SCHEDULED_EMAIL_BATCH_SIZE = 50


@app.task(name="teamshifts.dispatch_scheduled_emails")
def dispatch_scheduled_emails_task():
    with scopes_disabled():
        with transaction.atomic():
            due = list(
                TeamShiftsEmailQueue.objects.filter(
                    send_after__isnull=False,
                    send_after__lte=now(),
                    sent_at__isnull=True,
                )
                .select_for_update(skip_locked=True)
                .order_by("pk")
                .values_list("pk", "event_id")[:SCHEDULED_EMAIL_BATCH_SIZE]
            )

    for queue_pk, event_id in due:
        cache_key = f"teamshifts_mail_queue_{queue_pk}_enqueued"
        if cache.add(cache_key, True, timeout=300):
            send_queued_email.delay(event_id, queue_pk)
            logger.info("[TeamShifts] Dispatched scheduled email queue %s", queue_pk)


@app.task(
    base=ProfiledEventTask,
    name="teamshifts.send_queued_email",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_queued_email(self, event_id: int, queue_id: int):
    if isinstance(event_id, Event):
        event = event_id
        original_event_id = event.pk
    else:
        original_event_id = event_id
        try:
            event = Event.objects.get(pk=event_id)
        except Event.DoesNotExist:
            logger.error("[TeamShifts] Event %s not found for queue %s", event_id, queue_id)
            return

    try:
        with scope(event=event):
            queue = TeamShiftsEmailQueue.objects.select_related("event", "role_filter").filter(pk=queue_id, event=event).first()
            if queue is None:
                logger.debug("[TeamShifts] Queue %s not found or locked", queue_id)
                return
            if queue.sent_at:
                return
            if queue.send_after and queue.send_after > now():
                logger.debug("[TeamShifts] Queue %s not yet due, skipping", queue_id)
                return
            recipients = list(queue.recipients.select_related("user").all())

            if not recipients:
                logger.warning("[TeamShifts] Queue %s has no recipients", queue_id)
                queue.sent_at = now()
                queue.save(update_fields=["sent_at"])
                return

            subject = LazyI18nString(queue.subject)
            message = LazyI18nString(queue.message)
            locale = queue.locale or event.settings.locale

            partial_send = False
            for recipient in recipients:
                if recipient.sent_at:
                    continue

                claimed = type(recipient).objects.filter(pk=recipient.pk, sent_at__isnull=True).update(sent_at=now())

                if not claimed:
                    continue

                try:
                    ctx_kwargs = {"event": event}
                    if recipient.user:
                        ctx_kwargs["user"] = recipient.user
                    if queue.role_filter_id:
                        ctx_kwargs["role"] = queue.role_filter
                    context = get_email_context(**ctx_kwargs)
                    mail(
                        email=recipient.email,
                        subject=subject,
                        template=message,
                        context=context,
                        event=event,
                        locale=locale,
                        event_bcc=queue.bcc or None,
                        event_reply_to=queue.reply_to or None,
                        user=recipient.user,
                        auto_email=False,
                        sync_send=True,
                    )
                    recipient.error = ""
                    recipient.save(update_fields=["error"])
                except SendMailException as exc:
                    recipient.sent_at = None
                    recipient.error = str(exc)
                    recipient.save(update_fields=["error", "sent_at"])
                    logger.exception("[TeamShifts] Send failed for %s", recipient.email)
                    partial_send = True

            has_unsent = queue.recipients.filter(sent_at__isnull=True).exists()
            if not has_unsent:
                queue.sent_at = now()
                queue.save(update_fields=["sent_at"])
            else:
                partial_send = True
    except Exception as exc:
        logger.exception("[TeamShifts] Unexpected failure for queue %s", queue_id)
        try:
            self.retry(exc=exc, args=[original_event_id, queue_id])
        except MaxRetriesExceededError:
            logger.error("[TeamShifts] Max retries exceeded for queue %s", queue_id)
        return

    if partial_send:
        try:
            self.retry(
                exc=SendMailException("Partial send: some recipients failed"),
                args=[original_event_id, queue_id],
            )
        except MaxRetriesExceededError:
            logger.error("[TeamShifts] Max retries exceeded for queue %s (partial send)", queue_id)

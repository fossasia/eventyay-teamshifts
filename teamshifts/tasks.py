import logging

from celery.exceptions import MaxRetriesExceededError
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.email import get_email_context
from eventyay.base.models import Event
from eventyay.base.services.mail import SendMailException, mail
from eventyay.base.services.tasks import ProfiledEventTask
from eventyay.celery_app import app
from i18nfield.strings import LazyI18nString

logger = logging.getLogger(__name__)


@app.task(
    base=ProfiledEventTask,
    name="teamshifts.send_queued_email",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_queued_email(self, event_id: int, queue_id: int):
    from .models import TeamShiftsEmailQueue

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
            queue = TeamShiftsEmailQueue.objects.select_related("event").get(pk=queue_id, event=event)
            if queue.sent_at:
                return
            recipients = list(queue.recipients.all())
    except TeamShiftsEmailQueue.DoesNotExist:
        logger.error("[TeamShifts] Queue %s not found for event %s", queue_id, event_id)
        return

    if not recipients:
        logger.warning("[TeamShifts] Queue %s has no recipients", queue_id)
        with scope(event=event):
            queue.sent_at = now()
            queue.save(update_fields=["sent_at"])
        return

    subject = LazyI18nString(queue.subject)
    message = LazyI18nString(queue.message)
    locale = queue.locale or event.settings.locale

    try:
        for recipient in recipients:
            if recipient.sent_at:
                continue
            try:
                context = get_email_context(event=event, user=recipient.user) if recipient.user else get_email_context(event=event)
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
                with scope(event=event):
                    recipient.sent_at = now()
                    recipient.error = ""
                    recipient.save(update_fields=["sent_at", "error"])
            except SendMailException as exc:
                with scope(event=event):
                    recipient.error = str(exc)
                    recipient.save(update_fields=["error"])
                logger.exception("[TeamShifts] Send failed for %s", recipient.email)

        with scope(event=event):
            all_sent = all(r.sent_at for r in queue.recipients.all())
            queue.sent_at = now() if all_sent else None
            queue.save(update_fields=["sent_at"])
    except Exception as exc:
        logger.exception("[TeamShifts] Unexpected failure for queue %s", queue_id)
        try:
            self.retry(exc=exc, args=[original_event_id, queue_id])
        except MaxRetriesExceededError:
            logger.error("[TeamShifts] Max retries exceeded for queue %s", queue_id)

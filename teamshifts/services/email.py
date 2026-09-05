import logging
import re
from collections.abc import Iterable
from html import unescape

from django.db import transaction
from django.utils.html import strip_tags
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

_HTML_BODY_RE = re.compile(r"(?i)</?(p|br|div|ul|ol|li|strong|b|em|i|u|span|a|blockquote|h[1-6])\b")


def looks_like_email_html(value: str) -> bool:
    """Return True when *value* looks like Tiptap/email HTML rather than plain text."""
    if not value or "<" not in value:
        return False
    return bool(_HTML_BODY_RE.search(value))


def html_to_plain_text(html: str) -> str:
    """Convert email HTML to a readable plain-text MIME body."""
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n\n", text)
    text = strip_tags(text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
        return
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

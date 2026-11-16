import logging
from datetime import datetime, time, timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F
from django.utils.formats import date_format, time_format
from django.utils.html import escape
from django.utils.timezone import now
from django.utils.translation import gettext
from django_scopes import scope, scopes_disabled
from eventyay.base.email import get_email_context
from eventyay.base.i18n import language
from eventyay.base.services.mail import mail
from eventyay.celery_app import app
from eventyay.multidomain.urlreverse import mainreverse
from i18nfield.strings import LazyI18nString

from ..models import EmailTemplateRoles, ShiftAssignment, ShiftReminder
from .email import record_sent_email

logger = logging.getLogger(__name__)

PLUGIN_NAME = "teamshifts"
REMINDER_LEAD_TIME = timedelta(hours=24)
RETRY_INTERVAL = timedelta(minutes=30)
MAX_ATTEMPTS = 3


def my_shifts_url() -> str:
    return urljoin(settings.SITE_URL, mainreverse("plugins:teamshifts:my_shifts_global"))


def _reminder_assignments():
    return (
        ShiftAssignment.objects.filter(
            shift__event__plugins__contains=PLUGIN_NAME,
            shift__event__call_for_team_members__shift_schedule_published=True,
            team_member__is_active=True,
        )
        .exclude(team_member__email="")
        .select_related("shift", "shift__event", "shift__location", "shift__location__linked_room", "role", "team_member")
        .order_by("shift__start_time", "pk")
    )


def _signed_up_in_advance(assignment) -> bool:
    return assignment.assigned_at <= assignment.shift.start_time - REMINDER_LEAD_TIME


def due_event_user_days(current) -> set[tuple]:
    with scopes_disabled():
        due = _reminder_assignments().filter(
            assigned_at__lte=F("shift__start_time") - REMINDER_LEAD_TIME,
            shift__start_time__gt=current,
            shift__start_time__lte=current + REMINDER_LEAD_TIME,
        )
        return {(a.shift.event, a.team_member, a.shift.start_time.astimezone(a.shift.event.tz).date()) for a in due}


def day_assignments(event, user, day) -> list[ShiftAssignment]:
    day_start = datetime.combine(day, time.min, tzinfo=event.tz)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=event.tz)
    with scopes_disabled():
        return list(
            _reminder_assignments().filter(
                shift__event=event,
                team_member=user,
                shift__start_time__gte=day_start,
                shift__start_time__lt=day_end,
            )
        )


def _is_html(text: str) -> bool:
    return text.lstrip().startswith("<") or "data-variable=" in text


def format_shift_list(event, assignments, locale, *, html=False) -> str:
    lines = []
    for assignment in assignments:
        shift = assignment.shift
        start = shift.start_time.astimezone(event.tz)
        end = shift.end_time.astimezone(event.tz)
        location = shift.location.display_name(locale) if shift.location_id else shift.location_text
        lines.append(
            gettext("- %(date)s, %(start)s–%(end)s: %(name)s (Role: %(role)s, Location: %(location)s)")
            % {
                "date": date_format(start, "DATE_FORMAT"),
                "start": time_format(start, "TIME_FORMAT"),
                "end": time_format(end, "TIME_FORMAT"),
                "name": shift.name or gettext("Shift"),
                "role": assignment.role.name if assignment.role_id else gettext("Volunteer"),
                "location": location or gettext("No location set"),
            }
        )
    if html:
        return "<br>".join(escape(line) for line in lines)
    return "\n".join(lines)


def _claim_reminder(event, user, day) -> ShiftReminder | None:
    with scope(event=event):
        reminder, created = ShiftReminder.objects.get_or_create(event=event, user=user, day=day)
        if created:
            return reminder
        claimed = ShiftReminder.objects.filter(
            pk=reminder.pk,
            sent_at__isnull=True,
            attempts__lt=MAX_ATTEMPTS,
            claimed_at__lte=now() - RETRY_INTERVAL,
        ).update(claimed_at=now(), attempts=F("attempts") + 1)
    return reminder if claimed else None


def _mark_sent(reminder: ShiftReminder) -> None:
    with scope(event=reminder.event):
        ShiftReminder.objects.filter(pk=reminder.pk).update(sent_at=now())


def _get_reminder_template(event):
    try:
        template = event.call_for_team_members.get_mail_template(EmailTemplateRoles.SHIFT_REMINDER)
    except ObjectDoesNotExist:
        logger.warning("[TeamShifts] No CFM for event %s — skipping shift reminders", event.slug)
        return None
    return template if template.send_automatically else None


def _send_reminder(event, user, assignments, template) -> tuple[dict, str]:
    locale = user.locale or event.settings.locale
    body = LazyI18nString(template.body)
    with language(locale):
        shift_list = format_shift_list(event, assignments, locale, html=_is_html(str(body)))
    context = get_email_context(event=event, user=user, shift_list=shift_list)
    mail(
        email=user.email,
        subject=LazyI18nString(template.subject),
        template=body,
        context=context,
        event=event,
        locale=locale,
        user=user,
        auto_email=False,
        sync_send=True,
    )
    return context, locale


def send_due_shift_reminders(current=None) -> int:
    current = current or now()
    sent = 0
    templates = {}
    for event, user, day in sorted(due_event_user_days(current), key=lambda group: (group[0].pk, group[1].pk, group[2])):
        assignments = day_assignments(event, user, day)
        first_in_advance = next((a for a in assignments if _signed_up_in_advance(a)), None)
        if first_in_advance is None or first_in_advance.shift.start_time <= current:
            continue

        if event.pk not in templates:
            templates[event.pk] = _get_reminder_template(event)
        template = templates[event.pk]
        if template is None:
            continue

        reminder = _claim_reminder(event, user, day)
        if reminder is None:
            continue

        upcoming = [a for a in assignments if a.shift.start_time > current]
        try:
            context, locale = _send_reminder(event, user, upcoming, template)
        except Exception:
            logger.exception("[TeamShifts] Failed to send shift reminder to %s for %s", user.email, day)
            continue
        _mark_sent(reminder)
        record_sent_email(event, user, template.subject, template.body, context, locale, EmailTemplateRoles.SHIFT_REMINDER)
        sent += 1
        logger.info("[TeamShifts] Shift reminder sent to user %s for event %s on %s", user.pk, event.slug, day)
    return sent


@app.task(name="teamshifts.send_shift_reminders")
def send_shift_reminders_task():
    sent = send_due_shift_reminders()
    logger.info("[TeamShifts] Sent %s shift reminder emails", sent)

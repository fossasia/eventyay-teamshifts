import logging
from datetime import datetime, time, timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F
from django.utils.formats import date_format, time_format
from django.utils.timezone import now
from django.utils.translation import gettext
from django_scopes import scope, scopes_disabled
from eventyay.base.email import get_email_context
from eventyay.base.i18n import language
from eventyay.base.services.mail import mail
from i18nfield.strings import LazyI18nString

from ..models import EmailTemplateRoles, ShiftAssignment, ShiftReminder

logger = logging.getLogger(__name__)

PLUGIN_NAME = "teamshifts"
REMINDER_LEAD_TIME = timedelta(hours=24)
CLAIM_TIMEOUT = timedelta(minutes=30)


def _eligible_assignments():
    return (
        ShiftAssignment.objects.filter(
            shift__event__plugins__contains=PLUGIN_NAME,
            assigned_at__lte=F("shift__start_time") - REMINDER_LEAD_TIME,
            team_member__is_active=True,
        )
        .exclude(team_member__email="")
        .select_related("shift", "shift__event", "shift__location", "shift__location__linked_room", "role", "team_member")
        .order_by("shift__start_time", "pk")
    )


def due_event_user_days(current) -> set[tuple]:
    with scopes_disabled():
        due = _eligible_assignments().filter(
            shift__start_time__gt=current,
            shift__start_time__lte=current + REMINDER_LEAD_TIME,
        )
        return {(a.shift.event, a.team_member, a.shift.start_time.astimezone(a.shift.event.tz).date()) for a in due}


def day_assignments(event, user, day) -> list[ShiftAssignment]:
    day_start = datetime.combine(day, time.min, tzinfo=event.tz)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=event.tz)
    with scopes_disabled():
        return list(
            _eligible_assignments().filter(
                shift__event=event,
                team_member=user,
                shift__start_time__gte=day_start,
                shift__start_time__lt=day_end,
            )
        )


def format_shift_list(event, assignments, locale) -> str:
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
    return "\n".join(lines)


def _claim_reminder(event, user, day) -> ShiftReminder | None:
    with scope(event=event):
        reminder, created = ShiftReminder.objects.get_or_create(event=event, user=user, day=day)
        if created:
            return reminder
        reclaimed = ShiftReminder.objects.filter(pk=reminder.pk, sent_at__isnull=True, claimed_at__lte=now() - CLAIM_TIMEOUT).update(claimed_at=now())
    return reminder if reclaimed else None


def _release_reminder(reminder: ShiftReminder) -> None:
    with scope(event=reminder.event):
        ShiftReminder.objects.filter(pk=reminder.pk).delete()


def _mark_sent(reminder: ShiftReminder) -> None:
    with scope(event=reminder.event):
        ShiftReminder.objects.filter(pk=reminder.pk).update(sent_at=now())


def _send_reminder(event, user, assignments, template) -> None:
    locale = user.locale or event.settings.locale
    with language(locale):
        shift_list = format_shift_list(event, assignments, locale)
    context = get_email_context(event=event, user=user, shift_list=shift_list)
    mail(
        email=user.email,
        subject=LazyI18nString(template.subject),
        template=LazyI18nString(template.body),
        context=context,
        event=event,
        locale=locale,
        user=user,
        auto_email=False,
        sync_send=True,
    )


def send_due_shift_reminders(current=None) -> int:
    current = current or now()
    sent = 0
    templates = {}
    for event, user, day in sorted(due_event_user_days(current), key=lambda group: (group[0].pk, group[1].pk, group[2])):
        assignments = day_assignments(event, user, day)
        if not assignments or assignments[0].shift.start_time <= current:
            continue

        if event.pk not in templates:
            try:
                templates[event.pk] = event.call_for_team_members.get_mail_template(EmailTemplateRoles.SHIFT_REMINDER)
            except ObjectDoesNotExist:
                logger.warning("[TeamShifts] No CFM for event %s — skipping shift reminders", event.slug)
                templates[event.pk] = None
        template = templates[event.pk]
        if template is None:
            continue

        reminder = _claim_reminder(event, user, day)
        if reminder is None:
            continue

        try:
            _send_reminder(event, user, assignments, template)
        except Exception:
            _release_reminder(reminder)
            logger.exception("[TeamShifts] Failed to send shift reminder to %s for %s", user.email, day)
            continue
        _mark_sent(reminder)
        sent += 1
        logger.info("[TeamShifts] Shift reminder sent to user %s for event %s on %s", user.pk, event.slug, day)
    return sent

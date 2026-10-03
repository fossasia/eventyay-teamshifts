import logging
from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F
from django.utils.formats import date_format, time_format
from django.utils.timezone import now
from django.utils.translation import gettext
from django_scopes import scope, scopes_disabled
from eventyay.base.email import get_email_context
from eventyay.base.i18n import language
from eventyay.base.services.mail import SendMailException, mail
from i18nfield.strings import LazyI18nString

from ..models import EmailTemplateRoles, ShiftAssignment, ShiftReminder

logger = logging.getLogger(__name__)

PLUGIN_NAME = "teamshifts"
REMINDER_LEAD_TIME = timedelta(hours=24)
REMINDER_LOOKAHEAD = REMINDER_LEAD_TIME * 2


def _candidate_assignments(current):
    with scopes_disabled():
        return list(
            ShiftAssignment.objects.filter(
                shift__event__plugins__contains=PLUGIN_NAME,
                shift__start_time__gt=current,
                shift__start_time__lte=current + REMINDER_LOOKAHEAD,
                assigned_at__lte=F("shift__start_time") - REMINDER_LEAD_TIME,
                team_member__is_active=True,
            )
            .exclude(team_member__email="")
            .select_related("shift", "shift__event", "shift__event__organizer", "shift__location", "role", "team_member")
            .order_by("shift__start_time", "pk")
        )


def group_by_event_user_day(assignments) -> dict[tuple, list[ShiftAssignment]]:
    groups = defaultdict(list)
    for assignment in assignments:
        event = assignment.shift.event
        day = assignment.shift.start_time.astimezone(event.tz).date()
        groups[(event, assignment.team_member, day)].append(assignment)
    return groups


def format_shift_list(event, assignments) -> str:
    lines = []
    for assignment in assignments:
        shift = assignment.shift
        start = shift.start_time.astimezone(event.tz)
        end = shift.end_time.astimezone(event.tz)
        location = shift.location.name if shift.location_id else shift.location_text
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
    return reminder if created else None


def _release_reminder(reminder: ShiftReminder) -> None:
    with scope(event=reminder.event):
        ShiftReminder.objects.filter(pk=reminder.pk).delete()


def _send_reminder(event, user, assignments, template) -> None:
    locale = user.locale or event.settings.locale
    with language(locale):
        shift_list = format_shift_list(event, assignments)
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
    for (event, user, day), assignments in group_by_event_user_day(_candidate_assignments(current)).items():
        if assignments[0].shift.start_time > current + REMINDER_LEAD_TIME:
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
        except SendMailException:
            _release_reminder(reminder)
            logger.exception("[TeamShifts] Failed to send shift reminder to %s for %s", user.email, day)
            continue
        sent += 1
        logger.info("[TeamShifts] Shift reminder sent to user %s for event %s on %s", user.pk, event.slug, day)
    return sent

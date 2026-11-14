from django.utils.formats import date_format
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django_scopes import scope
from eventyay.base.i18n import language
from eventyay.base.models import Event, User
from eventyay.multidomain.urlreverse import build_absolute_uri

from ..models import Shift, ShiftAssignment


def _format_line(event: Event, shift, role, locale: str) -> str:
    start = shift.start_time.astimezone(event.tz)
    end = shift.end_time.astimezone(event.tz)
    end_text = date_format(end, "TIME_FORMAT") if start.date() == end.date() else date_format(end, "SHORT_DATETIME_FORMAT")
    parts = [
        date_format(start, "SHORT_DATE_FORMAT"),
        f"{date_format(start, 'TIME_FORMAT')} – {end_text}",
        shift.name or _("Shift"),
        _("Role: {role}").format(role=role.name if role else _("no specific role")),
    ]
    location = shift.location.display_name(locale) if shift.location_id else shift.location_text
    if location:
        parts.append(_("Location: {location}").format(location=location))
    return "- " + " · ".join(parts)


def _no_shifts_text(event: Event) -> str:
    return _("You currently have no shifts. You can sign up for shifts here:\n{url}").format(
        url=build_absolute_uri(event, "plugins:teamshifts:public_shift_schedule")
    )


def build_shift_summary(event: Event, user: User, locale: str = "en") -> str:
    with scope(event=event):
        assignments = list(
            ShiftAssignment.objects.filter(shift__event=event, team_member=user, shift__end_time__gte=now())
            .select_related("shift", "shift__location__linked_room", "role")
            .order_by("shift__start_time", "pk")
        )
    with language(locale, event.settings.region):
        if not assignments:
            return _no_shifts_text(event)
        return "\n".join(_format_line(event, a.shift, a.role, locale) for a in assignments)


def build_shift_summary_sample(event: Event, limit: int = 3) -> str:
    with scope(event=event):
        shifts_qs = Shift.objects.filter(event=event).select_related("location__linked_room").prefetch_related("role_assignments__role").order_by("start_time")
        shifts = list(shifts_qs.filter(end_time__gte=now())[:limit]) or list(shifts_qs[:limit])
        roles = [next(iter(shift.role_assignments.all()), None) for shift in shifts]
    locale = event.settings.locale
    with language(locale, event.settings.region):
        lines = [_format_line(event, shift, first.role if first else None, locale) for shift, first in zip(shifts, roles, strict=True)]
        return "\n".join(lines) if lines else _no_shifts_text(event)

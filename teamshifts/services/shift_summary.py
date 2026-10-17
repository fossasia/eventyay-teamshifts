from django.utils.timezone import now
from django.utils.translation import gettext as _
from django_scopes import scope
from eventyay.base.models import Event, User
from eventyay.helpers.urls import build_absolute_uri as build_global_uri
from eventyay.multidomain.urlreverse import build_absolute_uri

from ..models import Shift, ShiftAssignment


def my_shifts_url() -> str:
    return build_global_uri("plugins:teamshifts:my_shifts_global")


def has_shifts(event: Event, user: User) -> bool:
    with scope(event=event):
        return ShiftAssignment.objects.filter(shift__event=event, team_member=user).exists()


def _format_line(event: Event, shift, role, locale: str) -> str:
    start = shift.start_time.astimezone(event.tz)
    end = shift.end_time.astimezone(event.tz)
    end_text = f"{end:%H:%M}" if start.date() == end.date() else f"{end:%Y-%m-%d %H:%M}"
    parts = [
        f"{start:%Y-%m-%d}",
        f"{start:%H:%M} – {end_text}",
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
            ShiftAssignment.objects.filter(shift__event=event, team_member=user)
            .select_related("shift", "shift__location__linked_room", "role")
            .order_by("shift__start_time", "pk")
        )
    if not assignments:
        return _no_shifts_text(event)
    return "\n".join(_format_line(event, a.shift, a.role, locale) for a in assignments)


def build_shift_summary_sample(event: Event, limit: int = 3) -> str:
    with scope(event=event):
        shifts_qs = Shift.objects.filter(event=event).select_related("location__linked_room").prefetch_related("role_assignments__role").order_by("start_time")
        shifts = list(shifts_qs.filter(end_time__gte=now())[:limit]) or list(shifts_qs[:limit])
        lines = []
        for shift in shifts:
            first = next(iter(shift.role_assignments.all()), None)
            lines.append(_format_line(event, shift, first.role if first else None, event.settings.locale))
    return "\n".join(lines) if lines else _no_shifts_text(event)

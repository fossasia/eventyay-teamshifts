import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import vobject
from django.conf import settings
from django.utils.translation import gettext as _
from django_scopes import scopes_disabled

from ..models import ShiftAssignment, ShiftCalendarToken

PLUGIN_FILTER = "teamshifts"
EN_DASH = "–"


def get_member_assignments(user, event_id=None):
    with scopes_disabled():
        qs = (
            ShiftAssignment.objects.filter(
                team_member=user,
                shift__event__plugins__contains=PLUGIN_FILTER,
            )
            .select_related(
                "shift",
                "shift__event",
                "shift__event__organizer",
                "shift__location",
                "role",
                "assigned_by",
            )
            .order_by("shift__start_time")
        )
        if event_id is not None:
            qs = qs.filter(shift__event_id=event_id)
    return qs


def get_or_create_calendar_token(user):
    token, _created = ShiftCalendarToken.objects.get_or_create(user=user)
    return token


def get_calendar_token_user(token_value):
    if not token_value:
        return None
    token = ShiftCalendarToken.objects.select_related("user").filter(token=token_value).first()
    return token.user if token else None


def _shift_summary(assignment):
    shift = assignment.shift
    label = shift.name or (shift.location.name if shift.location_id else "") or _("Shift")
    if assignment.role_id:
        return f"{label} {EN_DASH} {assignment.role.name}"
    return label


def _shift_location(shift):
    if shift.location_id:
        return shift.location.name
    return shift.location_text


def build_shift_calendar(assignments, my_shifts_url):
    cal = vobject.iCalendar()
    cal.add("prodid").value = "-//eventyay//{}//".format(settings.INSTANCE_NAME.replace(" ", "_"))
    cal.add("x-wr-calname").value = _("My Shifts")
    cal.add("refresh-interval;value=duration").value = "PT1H"
    cal.add("x-published-ttl").value = "PT1H"
    host = urlparse(my_shifts_url).netloc
    stamp = datetime.datetime.now(ZoneInfo("UTC"))

    for assignment in assignments:
        shift = assignment.shift
        event = shift.event
        tz = event.tz

        vevent = cal.add("vevent")
        vevent.add("uid").value = f"teamshifts-{shift.pk}-{assignment.team_member_id}@{host}"
        vevent.add("dtstamp").value = stamp
        vevent.add("summary").value = _shift_summary(assignment)
        vevent.add("dtstart").value = shift.start_time.astimezone(tz)
        vevent.add("dtend").value = shift.end_time.astimezone(tz)
        location = _shift_location(shift)
        if location:
            vevent.add("location").value = location
        vevent.add("description").value = "\n".join(
            [
                _("Event: {event}").format(event=event.name),
                _("My Shifts: {url}").format(url=my_shifts_url),
            ]
        )
    return cal.serialize()

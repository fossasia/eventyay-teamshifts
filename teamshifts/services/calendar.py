from django_scopes import scopes_disabled

from ..models import ShiftAssignment, ShiftCalendarToken


def get_member_assignments(user, event_id=None):
    with scopes_disabled():
        qs = (
            ShiftAssignment.objects.filter(
                team_member=user,
                shift__event__plugins__contains="teamshifts",
            )
            .select_related(
                "shift",
                "shift__event",
                "shift__event__organizer",
                "shift__location",
                "role",
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
    token = ShiftCalendarToken.objects.select_related("user").filter(token=token_value).first()
    return token.user if token else None

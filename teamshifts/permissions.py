import functools

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext as _
from django_scopes import scopes_disabled
from eventyay.base.models.organizer import Team

COORDINATOR_PERMISSIONS = frozenset(
    {
        "can_teamshifts_manage_applicants",
        "can_teamshifts_create_roles",
    }
)

LEAD_PERMISSIONS = frozenset(
    {
        "can_teamshifts_create_shifts",
        "can_teamshifts_send_emails",
    }
)

ROLE_LEVELS = {  # empty string = no teamshifts access (matches model default)
    "coordinator": 2,
    "lead": 1,
    "": 0,
}


def _get_teamshifts_role(user, organizer, event, request=None):
    """Return the highest teamshifts_role the user holds for this event."""
    if not Team.objects.filter(
        organizer=organizer,
        members=user,
    ).filter(Q(all_events=True) | Q(limit_events=event)).exclude(teamshifts_role="").exists() and user.has_event_permission(
        organizer, event, "can_change_event_settings", request=request
    ):
        return "coordinator"
    with scopes_disabled():
        teams = (
            Team.objects.filter(
                organizer=organizer,
                members=user,
            )
            .filter(Q(all_events=True) | Q(limit_events=event))
            .exclude(teamshifts_role="")
        )
        best = ""
        for team in teams:
            role = team.teamshifts_role
            if ROLE_LEVELS.get(role, 0) > ROLE_LEVELS.get(best, 0):
                best = role
        return best


def has_any_teamshifts_permission(user, organizer, event, request=None):
    return _get_teamshifts_role(user, organizer, event, request=request) != ""


def has_teamshifts_permission(user, organizer, event, permission, request=None):
    """Check if user has a specific teamshifts permission for the event."""
    role = _get_teamshifts_role(user, organizer, event, request=request)
    if role == "coordinator":
        return True
    if role == "lead":
        return permission in LEAD_PERMISSIONS
    return False


def can_view_email_addresses(user, organizer, event, request=None):
    """Check if the user can see volunteer email addresses."""
    role = _get_teamshifts_role(user, organizer, event, request=request)
    if role == "coordinator":
        return True
    if role == "lead":
        with scopes_disabled():
            teams = Team.objects.filter(
                organizer=organizer,
                members=user,
                teamshifts_role="lead",
            ).filter(Q(all_events=True) | Q(limit_events=event))
            # If ANY of the user's lead teams does NOT hide emails, they can see them
            return teams.filter(hide_teamshifts_emails=False).exists()
    return False


def get_allowed_role_ids(user, organizer, event, request=None):
    role = _get_teamshifts_role(user, organizer, event, request=request)
    if role == "coordinator":
        return None  # Full access to all roles
    if role != "lead":
        return set()
    with scopes_disabled():
        teams = Team.objects.filter(
            organizer=organizer,
            members=user,
            teamshifts_role="lead",
        ).filter(Q(all_events=True) | Q(limit_events=event))
        allowed = set()
        for team in teams:
            if team.all_teamshifts_roles:
                return None
            limit = team.limit_teamshifts_roles
            if isinstance(limit, list):
                allowed.update(limit)
        return allowed


def can_act_on_role(user, organizer, event, role_pk, request=None):
    allowed = get_allowed_role_ids(user, organizer, event, request=request)
    if allowed is None:
        return True
    return role_pk in allowed


def teamshifts_permission_required(permission):
    def decorator(function):
        @functools.wraps(function)
        def wrapper(request, *args, **kw):
            if not request.user.is_authenticated:
                raise PermissionDenied()

            if permission:
                if has_teamshifts_permission(request.user, request.organizer, request.event, permission, request=request):
                    return function(request, *args, **kw)
            else:
                if has_any_teamshifts_permission(request.user, request.organizer, request.event, request=request):
                    return function(request, *args, **kw)

            raise PermissionDenied(_("You do not have permission to view this content."))

        return wrapper

    return decorator


class TeamShiftsPermissionRequiredMixin:
    permission = None

    @classmethod
    def as_view(cls, **initkwargs):
        view = super().as_view(**initkwargs)
        return teamshifts_permission_required(cls.permission)(view)

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _


def has_any_teamshifts_permission(user, organizer, event, request=None):
    if user.has_event_permission(organizer, event, "can_change_event_settings", request=request):
        return True
    teamshifts_perms = [
        "can_teamshifts_manage_applicants",
        "can_teamshifts_create_shifts",
        "can_teamshifts_create_roles",
        "can_teamshifts_send_emails",
        "can_teamshifts_view_email_addresses",
    ]
    return any(user.has_event_permission(organizer, event, p, request=request) for p in teamshifts_perms)


def teamshifts_permission_required(permission):
    def decorator(function):
        def wrapper(request, *args, **kw):
            if not request.user.is_authenticated:
                raise PermissionDenied()

            if request.user.has_event_permission(request.organizer, request.event, "can_change_event_settings", request=request):
                return function(request, *args, **kw)

            if permission:
                allowed = request.user.has_event_permission(request.organizer, request.event, permission, request=request)
                if allowed:
                    return function(request, *args, **kw)
            else:
                if has_any_teamshifts_permission(request.user, request.organizer, request.event, request=request):
                    return function(request, *args, **kw)

            raise PermissionDenied(_('You do not have permission to view this content.'))

        return wrapper

    return decorator


class TeamShiftsPermissionRequiredMixin:
    permission = None

    @classmethod
    def as_view(cls, **initkwargs):
        view = super().as_view(**initkwargs)
        return teamshifts_permission_required(cls.permission)(view)

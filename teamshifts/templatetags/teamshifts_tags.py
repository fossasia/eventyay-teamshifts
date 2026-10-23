from django import template
from django_scopes import scope

from ..models import TeamShiftsEmailQueue

register = template.Library()


@register.simple_tag(takes_context=True)
def teamshifts_outbox_count(context):
    request = context.get("request")
    if not request or not hasattr(request, "event"):
        return 0
    with scope(event=request.event):
        return TeamShiftsEmailQueue.objects.filter(event=request.event, sent_at__isnull=True, is_draft=False, user__isnull=False).count()


@register.filter
def dict_get(d, key):
    """Look up a dictionary value by key. Returns None if missing."""
    if isinstance(d, dict):
        return d.get(key)
    return None


@register.filter
def in_set(value, container):
    """Check if value is in a container (set, list, etc.)."""
    return value in container if container else False

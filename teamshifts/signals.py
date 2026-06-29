from django.dispatch import receiver
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets
from eventyay.presale.signals import header_nav_tabs

from .models import CallForTeamMembers


@receiver(event_dashboard_widgets, dispatch_uid="teamshifts_dashboard_widget")
def teamshifts_dashboard_widget(sender, subevent=None, lazy=False, request=None, **kwargs):
    if request is None or not request.user.has_event_permission(request.organizer, sender, "can_change_event_settings", request=request):
        return []
    return [
        {
            "content": '<div class="numwidget"><span class="num">-</span><span class="text">{}</span></div>'.format(str(_("TeamShifts"))),
            "display_size": "small",
            "priority": 80,
            "url": reverse(
                "plugins:teamshifts:dashboard",
                kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
            ),
        }
    ]


@receiver(event_dashboard_components, dispatch_uid="teamshifts_dashboard_component")
def teamshifts_dashboard_component(sender, request=None, **kwargs):
    if request is None or not request.user.has_event_permission(request.organizer, sender, "can_change_event_settings", request=request):
        return ""
    url = reverse(
        "plugins:teamshifts:dashboard",
        kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
    )
    return format_html(
        '<div class="panel panel-default widget-container widget-small no-padding last-column">'
        '<div class="panel-heading"><h3 class="panel-title">{}</h3></div>'
        '<div class="panel-body"><p>{}</p><p>{} <a href="{}">{}</a></p></div>'
        "</div>",
        str(_("TeamShifts")),
        str(_("Manage event teams, define team roles, review applications, and schedule shifts.")),
        str(_("Go to")),
        url,
        str(_("TeamShifts Dashboard")),
    )


@receiver(header_nav_tabs, dispatch_uid="teamshifts_header_nav_tab")
def teamshifts_header_nav_tab(sender, request=None, **kwargs):
    try:
        cfm = sender.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        return ""
    if not cfm.active or not cfm.show_on_menu:
        return ""
    apply_url = reverse(
        "plugins:teamshifts:apply",
        kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
    )
    is_active = request is not None and getattr(request, "resolver_match", None) is not None and request.resolver_match.url_name == "apply"
    tab_title = cfm.title if cfm.is_open else format_html("{} ({})", cfm.title, _("Closed"))
    return format_html(
        '<a href="{}" class="header-tab {}"><i class="fa fa-users"></i> {}</a>',
        apply_url,
        "active" if is_active else "",
        tab_title,
    )

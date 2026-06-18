from django.dispatch import receiver
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets
from eventyay.presale.signals import front_page_top

from .models import CallForTeamMembers


@receiver(event_dashboard_widgets, dispatch_uid="teamshifts_dashboard_widget")
def teamshifts_dashboard_widget(sender, subevent=None, lazy=False, **kwargs):
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


@receiver(front_page_top, dispatch_uid="teamshifts_front_page_top")
def teamshifts_front_page_top(sender, request, **kwargs):
    try:
        cfm = sender.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        return ""
    if not cfm.active:
        return ""
    apply_url = reverse(
        "plugins:teamshifts:apply",
        kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
    )
    description_html = format_html("<p>{}</p>", cfm.description) if cfm.description else ""
    return format_html(
        '<div class="panel panel-info" id="teamshifts-cfm-panel">'
        '<div class="panel-heading"><h3 class="panel-title">'
        '<span class="fa fa-users"></span> {title}'
        "</h3></div>"
        '<div class="panel-body">{description}<a href="{url}" class="btn btn-primary btn-lg">{cta}</a></div>'
        "</div>",
        title=cfm.title,
        description=description_html,
        url=apply_url,
        cta=str(_("Apply now")),
    )

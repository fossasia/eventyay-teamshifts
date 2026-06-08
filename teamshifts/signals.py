from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets


@receiver(event_dashboard_widgets, dispatch_uid="teamshifts_dashboard_widget")
def teamshifts_dashboard_widget(sender, subevent=None, lazy=False, **kwargs):
    return [
        {
            "content": ('<div class="numwidget"><span class="num">-</span><span class="text">{}</span></div>').format(
                str(_("TeamShifts"))
            ),
            "display_size": "small",
            "priority": 80,
            "url": reverse(
                "plugins:teamshifts:dashboard",
                kwargs={
                    "organizer": sender.organizer.slug,
                    "event": sender.slug,
                },
            ),
        }
    ]


@receiver(event_dashboard_components, dispatch_uid="teamshifts_dashboard_component")
def teamshifts_dashboard_component(sender, request=None, **kwargs):
    url = reverse(
        "plugins:teamshifts:dashboard",
        kwargs={
            "organizer": sender.organizer.slug,
            "event": sender.slug,
        },
    )
    return (
        '<div class="panel panel-default widget-container widget-small no-padding last-column">'
        '<div class="panel-heading">'
        '<h3 class="panel-title">{title}</h3>'
        "</div>"
        '<div class="panel-body">'
        "<p>{description}</p>"
        "<p>{go_to} <a href=\"{url}\">{link_text}</a></p>"
        "</div>"
        "</div>"
    ).format(
        title=str(_("TeamShifts")),
        description=str(
            _(
                "Manage event teams, define team roles, review team member"
                " applications, and build a shift schedule for your event staff."
            )
        ),
        go_to=str(_("Go to")),
        url=url,
        link_text=str(_("TeamShifts Dashboard")),
    )

from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from eventyay.control.signals import event_dashboard_widgets


@receiver(event_dashboard_widgets, dispatch_uid="teamshifts_dashboard_widget")
def teamshifts_dashboard_widget(sender, subevent=None, lazy=False, **kwargs):
    return [
        {
            "content": ('<div class="numwidget"><span class="num">-</span><span class="text">{}</span></div>').format(str(_("TeamShifts"))),
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

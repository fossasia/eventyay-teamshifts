from django.views.generic import TemplateView
from eventyay.control.permissions import EventPermissionRequiredMixin


class TeamShiftsDashboard(EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
    template_name = "teamshifts/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.request.event
        return ctx

from django.urls import path
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from . import views

urlpatterns = [
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/",
        views.TeamShiftsDashboard.as_view(),
        name="dashboard",
    ),
]

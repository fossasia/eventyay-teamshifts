from django.urls import path
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from . import views

urlpatterns = [
    # Organiser views (require can_change_event_settings)
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/",
        views.TeamShiftsDashboard.as_view(),
        name="dashboard",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/",
        views.CFMSettingsView.as_view(),
        name="cfm_settings",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/roles/",
        views.TeamRoleListView.as_view(),
        name="roles",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/roles/<int:pk>/delete/",
        views.TeamRoleDeleteView.as_view(),
        name="role_delete",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/applications/",
        views.ApplicationListView.as_view(),
        name="applications",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/applications/<int:pk>/status/",
        views.ApplicationStatusView.as_view(),
        name="application_status",
    ),
    # Public views (login required, no organiser permission)
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/apply/",
        views.PublicApplyView.as_view(),
        name="apply",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/apply/thanks/",
        views.PublicApplyThanksView.as_view(),
        name="apply_thanks",
    ),
]

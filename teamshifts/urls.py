from django.urls import path
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from . import views

event_patterns = [
    path(
        "teamshifts/apply/",
        views.PublicApplyView.as_view(),
        name="apply",
    ),
    path(
        "teamshifts/apply/thanks/",
        views.PublicApplyThanksView.as_view(),
        name="apply_thanks",
    ),
]

urlpatterns = [
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
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/preview/",
        views.CFMDescriptionPreviewView.as_view(),
        name="cfm_description_preview",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/application-form/",
        views.CFMApplicationFormView.as_view(),
        name="cfm_application_form",
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
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/questions/new/",
        views.QuestionEditView.as_view(),
        name="question_create",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/questions/reorder/",
        views.QuestionReorderView.as_view(),
        name="question_reorder",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/<int:pk>/toggle/",
        views.QuestionToggleView.as_view(),
        name="question_toggle",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/questions/<int:pk>/edit/",
        views.QuestionEditView.as_view(),
        name="question_edit",
    ),
    path(
        "teamshifts/event/<orgslug:organizer>/<slug:event>/settings/questions/<int:pk>/delete/",
        views.QuestionDeleteView.as_view(),
        name="question_delete",
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
]

import eventyay.control.views.dashboards  # noqa: F401 (ensure standard widget receivers are registered)
import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import Team, User
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets

import teamshifts.signals
from teamshifts.signals import teamshifts_dashboard_component


def _build_request(factory, user, organizer):
    request = factory.get("/")
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request.user = user
    request.organizer = organizer
    return request


def test_dashboard_widget_signal_receiver_removed():
    """Verify teamshifts_dashboard_widget has been completely removed."""
    assert not hasattr(teamshifts.signals, "teamshifts_dashboard_widget")


@pytest.mark.django_db
def test_event_dashboard_widgets_has_no_teamshifts_numwidget(event, user):
    """Verify emitting event_dashboard_widgets signal returns no TeamShifts numwidgets."""
    factory = RequestFactory()
    request = _build_request(factory, user, event.organizer)

    with scope(event=event, organizer=event.organizer):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)

        responses = event_dashboard_widgets.send(sender=event, subevent=None, lazy=False, request=request)
        for _receiver, response in responses:
            if isinstance(response, list):
                for widget in response:
                    content = widget.get("content", "")
                    assert "TeamShifts" not in content


@pytest.mark.django_db
def test_teamshifts_dashboard_component_permissions(event, user):
    """Verify teamshifts_dashboard_component renders only when user has permission."""
    # When request is None
    assert teamshifts_dashboard_component(event, request=None) == ""

    # When user has no permissions
    factory = RequestFactory()
    request_unauth = _build_request(factory, user, event.organizer)
    with scope(event=event, organizer=event.organizer):
        assert teamshifts_dashboard_component(event, request=request_unauth) == ""

    # When user has permission
    perm_user = User.objects.create_user(email="authorized@example.com", password="secret")
    with scope(event=event, organizer=event.organizer):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Authorized Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(perm_user)

        request_auth = _build_request(factory, perm_user, event.organizer)
        component_html = teamshifts_dashboard_component(event, request=request_auth)
        expected_url = reverse(
            "plugins:teamshifts:dashboard",
            kwargs={"organizer": event.organizer.slug, "event": event.slug},
        )
        assert "panel panel-default widget-container" in component_html
        assert "TeamShifts" in component_html
        assert expected_url in component_html


@pytest.mark.django_db
def test_event_dashboard_components_signal_still_renders_teamshifts(event, user):
    """Verify event_dashboard_components signal still includes the TeamShifts panel."""
    factory = RequestFactory()
    request = _build_request(factory, user, event.organizer)

    with scope(event=event, organizer=event.organizer):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)

        responses = event_dashboard_components.send(sender=event, request=request)
        contents = [response for _receiver, response in responses if response]
        assert any("TeamShifts" in content and "widget-container" in content for content in contents)

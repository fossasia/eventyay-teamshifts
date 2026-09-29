import eventyay.control.views.dashboards  # noqa: F401 (ensure standard widget receivers are registered)
import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import Team, User
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets, nav_organizer

import teamshifts.signals
from teamshifts.signals import teamshifts_dashboard_component, teamshifts_nav_organizer


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


@pytest.mark.django_db
def test_teamshifts_nav_organizer_unauthenticated(organizer):
    """Verify nav_organizer returns empty list for unauthenticated requests."""
    assert teamshifts_nav_organizer(organizer, request=None) == []

    factory = RequestFactory()
    request = factory.get("/")
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    assert teamshifts_nav_organizer(organizer, request=request) == []


@pytest.mark.django_db
def test_teamshifts_nav_organizer_no_events_with_plugin(organizer, user):
    """Verify nav_organizer returns empty list when no events have teamshifts enabled."""
    factory = RequestFactory()
    request = _build_request(factory, user, organizer)

    team = Team.objects.create(
        organizer=organizer,
        name="Orga Admin",
        can_change_organizer_settings=True,
        all_events=True,
    )
    team.members.add(user)

    # Organizer has no events at all -> plugin not enabled
    assert teamshifts_nav_organizer(organizer, request=request) == []


@pytest.mark.django_db
def test_teamshifts_nav_organizer_user_without_permission(organizer, event, user):
    """Verify nav_organizer returns empty list when user lacks TeamShifts access."""
    event.plugins = "teamshifts"
    event.save(update_fields=["plugins"])

    factory = RequestFactory()
    request = _build_request(factory, user, organizer)

    # User has no team membership or permission
    assert teamshifts_nav_organizer(organizer, request=request) == []


@pytest.mark.django_db
def test_teamshifts_nav_organizer_with_permission_and_active_state(organizer, event, user):
    """Verify nav_organizer returns entry point when user has access and plugin is enabled."""
    event.plugins = "teamshifts"
    event.save(update_fields=["plugins"])

    team = Team.objects.create(
        organizer=organizer,
        name="TeamShifts Coordinators",
        teamshifts_role="coordinator",
        all_events=True,
    )
    team.members.add(user)

    factory = RequestFactory()
    request = _build_request(factory, user, organizer)

    class FakeResolverMatch:
        url_name = "other"
        namespace = ""

    request.resolver_match = FakeResolverMatch()

    nav = teamshifts_nav_organizer(organizer, request=request)
    assert len(nav) == 1
    expected_url = reverse("plugins:teamshifts:organizer_dashboard", kwargs={"organizer": organizer.slug})
    assert nav[0]["label"] == "TeamShifts"
    assert nav[0]["url"] == expected_url
    assert nav[0]["icon"] == "users"
    assert nav[0]["active"] is False

    # When on the organizer dashboard view, active is True
    request.resolver_match.url_name = "organizer_dashboard"
    nav_active = teamshifts_nav_organizer(organizer, request=request)
    assert nav_active[0]["active"] is True

    # Verify signal emission also returns the item
    responses = nav_organizer.send(sender=organizer, request=request, organizer=organizer)
    flattened = [item for _recv, items in responses if items for item in items]
    assert any(item["url"] == expected_url for item in flattened)

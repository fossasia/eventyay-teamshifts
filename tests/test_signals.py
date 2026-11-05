import eventyay.control.views.dashboards  # noqa: F401 (ensure standard widget receivers are registered)
import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import Team, User
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets
from eventyay.presale.signals import header_nav_tabs

import teamshifts.signals
from teamshifts.models import ApplicationStatus, CallForTeamMembers, TeamMemberApplication
from teamshifts.signals import teamshifts_dashboard_component, teamshifts_public_schedule_nav_tab


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
def test_public_schedule_nav_tab_hidden_when_no_cfm(event, user):
    """Verify Shift Schedule tab is not rendered when no CallForTeamMembers exists."""
    factory = RequestFactory()
    request = _build_request(factory, user, event.organizer)

    with scope(event=event, organizer=event.organizer):
        # User is accepted team member
        TeamMemberApplication.objects.create(
            event=event,
            user=user,
            status=ApplicationStatus.ACCEPTED,
        )
        assert teamshifts_public_schedule_nav_tab(event, request=request) == ""


@pytest.mark.django_db
def test_public_schedule_nav_tab_hidden_when_unpublished(event, user):
    """Verify Shift Schedule tab is hidden for accepted members and organizers when unpublished."""
    factory = RequestFactory()
    volunteer_request = _build_request(factory, user, event.organizer)

    orga_user = User.objects.create_user(email="orga@example.com", password="secret")
    orga_request = _build_request(factory, orga_user, event.organizer)

    with scope(event=event, organizer=event.organizer):
        CallForTeamMembers.objects.create(event=event, title="Join", active=True, shift_schedule_published=False)

        TeamMemberApplication.objects.create(
            event=event,
            user=user,
            status=ApplicationStatus.ACCEPTED,
        )

        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(orga_user)

        # Direct receiver call
        assert teamshifts_public_schedule_nav_tab(event, request=volunteer_request) == ""
        assert teamshifts_public_schedule_nav_tab(event, request=orga_request) == ""

        # Via signal dispatch
        vol_responses = header_nav_tabs.send(sender=event, request=volunteer_request)
        vol_tabs = [r for _rec, r in vol_responses if r]
        assert not any("Shift Schedule" in tab for tab in vol_tabs)

        orga_responses = header_nav_tabs.send(sender=event, request=orga_request)
        orga_tabs = [r for _rec, r in orga_responses if r]
        assert not any("Shift Schedule" in tab for tab in orga_tabs)


@pytest.mark.django_db
def test_public_schedule_nav_tab_visible_when_published(event, user):
    """Verify Shift Schedule tab is visible only when published, and only to authorized users."""
    factory = RequestFactory()
    volunteer_request = _build_request(factory, user, event.organizer)

    orga_user = User.objects.create_user(email="orga2@example.com", password="secret")
    orga_request = _build_request(factory, orga_user, event.organizer)

    regular_user = User.objects.create_user(email="regular@example.com", password="secret")
    regular_request = _build_request(factory, regular_user, event.organizer)

    anon_request = factory.get("/")
    SessionMiddleware(lambda req: None).process_request(anon_request)
    anon_request.session.save()
    anon_request.user = AnonymousUser()
    anon_request.organizer = event.organizer

    with scope(event=event, organizer=event.organizer):
        CallForTeamMembers.objects.create(event=event, title="Join", active=True, shift_schedule_published=True)

        TeamMemberApplication.objects.create(
            event=event,
            user=user,
            status=ApplicationStatus.ACCEPTED,
        )

        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team 2",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(orga_user)

        # Accepted volunteer sees tab
        vol_tab = teamshifts_public_schedule_nav_tab(event, request=volunteer_request)
        assert "Shift Schedule" in vol_tab
        assert "header-tab" in vol_tab

        # Organizer sees tab
        orga_tab = teamshifts_public_schedule_nav_tab(event, request=orga_request)
        assert "Shift Schedule" in orga_tab

        # Regular user (not accepted member, not organizer) does not see tab
        assert teamshifts_public_schedule_nav_tab(event, request=regular_request) == ""

        # Anonymous user does not see tab
        assert teamshifts_public_schedule_nav_tab(event, request=anon_request) == ""

        # None request does not error
        assert teamshifts_public_schedule_nav_tab(event, request=None) == ""


@pytest.mark.django_db
def test_public_schedule_nav_tab_toggles_with_publish_unpublish(event, user):
    """Verify Shift Schedule tab appears upon publish and disappears upon unpublish."""
    factory = RequestFactory()
    volunteer_request = _build_request(factory, user, event.organizer)

    with scope(event=event, organizer=event.organizer):
        cfm = CallForTeamMembers.objects.create(event=event, title="Join", active=True, shift_schedule_published=False)
        TeamMemberApplication.objects.create(
            event=event,
            user=user,
            status=ApplicationStatus.ACCEPTED,
        )

        # Initially unpublished -> hidden
        assert teamshifts_public_schedule_nav_tab(event, request=volunteer_request) == ""

        # Organizer publishes schedule -> visible
        cfm.shift_schedule_published = True
        cfm.save(update_fields=["shift_schedule_published"])
        tab_html = teamshifts_public_schedule_nav_tab(event, request=volunteer_request)
        assert "Shift Schedule" in tab_html

        # Organizer unpublishes schedule -> hidden again
        cfm.shift_schedule_published = False
        cfm.save(update_fields=["shift_schedule_published"])
        assert teamshifts_public_schedule_nav_tab(event, request=volunteer_request) == ""

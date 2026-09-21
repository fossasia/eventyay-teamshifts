from datetime import timedelta

import pytest
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import User

from teamshifts.forms import CertificateSettingsForm
from teamshifts.models import (
    ApplicationStatus,
    CertificateMatchMode,
    CertificateSettings,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    TeamMemberApplication,
)
from teamshifts.services.certificates import completed_shift_count, member_qualifies


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="secret", fullname="Jane Member")


@pytest.fixture
def application(event, member):
    with scope(event=event):
        return TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
        )


@pytest.fixture
def settings_obj(event):
    with scope(event=event):
        return CertificateSettings.objects.create(event=event, require_arrived=True, require_min_shifts=False)


def _assign_shift(event, member, *, ended=False, name="Door"):
    location, _created = ShiftLocation.objects.get_or_create(event=event, name="Hall")
    shift = Shift.objects.create(
        event=event,
        name=name,
        location=location,
        start_time=now(),
        end_time=now() + timedelta(hours=2),
    )
    assignment = ShiftAssignment.objects.create(shift=shift, team_member=member)
    if ended:
        assignment.ended_at = now()
        assignment.save(update_fields=["ended_at"])
    return assignment


@pytest.mark.django_db
def test_arrived_only_qualifies(event, application, settings_obj):
    with scope(event=event):
        assert member_qualifies(application, settings_obj) is False
        application.arrived = True
        application.save(update_fields=["arrived"])
        assert member_qualifies(application, settings_obj) is True


@pytest.mark.django_db
def test_completed_shifts_require_ended_at(event, application, member, settings_obj):
    with scope(event=event):
        settings_obj.require_arrived = False
        settings_obj.require_min_shifts = True
        settings_obj.min_shifts = 1
        settings_obj.save()

        _assign_shift(event, member, ended=False, name="Door 1")
        assert completed_shift_count(application) == 0
        assert member_qualifies(application, settings_obj) is False

        _assign_shift(event, member, ended=True, name="Door 2")
        assert completed_shift_count(application) == 1
        assert member_qualifies(application, settings_obj) is True


@pytest.mark.django_db
def test_match_mode_any(event, application, member, settings_obj):
    with scope(event=event):
        settings_obj.require_arrived = True
        settings_obj.require_min_shifts = True
        settings_obj.min_shifts = 1
        settings_obj.match_mode = CertificateMatchMode.ANY
        settings_obj.save()

        application.arrived = True
        application.save(update_fields=["arrived"])
        assert member_qualifies(application, settings_obj) is True

        application.arrived = False
        application.save(update_fields=["arrived"])
        _assign_shift(event, member, ended=True)
        assert member_qualifies(application, settings_obj) is True


@pytest.mark.django_db
def test_match_mode_all_requires_both(event, application, member, settings_obj):
    with scope(event=event):
        settings_obj.require_arrived = True
        settings_obj.require_min_shifts = True
        settings_obj.min_shifts = 1
        settings_obj.match_mode = CertificateMatchMode.ALL
        settings_obj.save()

        application.arrived = True
        application.save(update_fields=["arrived"])
        assert member_qualifies(application, settings_obj) is False

        _assign_shift(event, member, ended=True)
        assert member_qualifies(application, settings_obj) is True


@pytest.mark.django_db
def test_form_requires_at_least_one_condition(event, settings_obj):
    with scope(event=event):
        form = CertificateSettingsForm(
            data={
                "min_shifts": "1",
                "match_mode": "all",
                "trigger": "auto",
            },
            instance=settings_obj,
        )
        assert form.is_valid() is False


def test_hex_to_rgba():
    from teamshifts.pdf import hex_to_rgba

    assert hex_to_rgba("#c0392b") == [192, 57, 43, 1]
    assert hex_to_rgba("#1B365D") == [27, 54, 93, 1]
    assert hex_to_rgba("#fff") == [255, 255, 255, 1]
    assert hex_to_rgba("00ff00") == [0, 255, 0, 1]
    assert hex_to_rgba("#fffzzz") is None
    assert hex_to_rgba("") is None
    assert hex_to_rgba(None) is None
    assert hex_to_rgba("invalid") is None


@pytest.mark.django_db
def test_preview_context_event_color(event):
    from teamshifts.pdf import preview_context

    with scope(event=event):
        event.primary_color = "#3498db"
        event.save(update_fields=["primary_color"])
        ctx = preview_context(event)
        assert ctx["_event_color"] == event.visible_primary_color

        event.primary_color = None
        event.save(update_fields=["primary_color"])
        ctx = preview_context(event)
        assert ctx["_event_color"] == event.visible_primary_color


@pytest.mark.django_db
def test_application_context_event_color(event, application):
    from teamshifts.services.certificates import application_context

    with scope(event=event):
        ctx = application_context(application)
        assert "_event_color" in ctx
        assert ctx["_event_color"] == event.visible_primary_color


@pytest.mark.django_db
def test_certificate_renderer_applies_event_color_to_default_layout(event):
    from unittest.mock import MagicMock

    from teamshifts.pdf import CertificateRenderer, default_layout, hex_to_rgba

    with scope(event=event, organizer=event.organizer):
        event_color = "#c0392b"
        ctx = {"_event_color": event_color}
        layout = default_layout()
        renderer = CertificateRenderer(event, layout, None, ctx)

        drawn_objects = []
        renderer._draw_textarea = MagicMock(side_effect=lambda c, op, order, o: drawn_objects.append(o))
        renderer._draw_imagearea = MagicMock()
        renderer._draw_poweredby = MagicMock()

        canvas = MagicMock()
        renderer.draw_page(canvas, show_page=False)

        expected_color = hex_to_rgba(event_color)
        title_obj = next(o for o in drawn_objects if o.get("content") == "certificate_title")
        member_obj = next(o for o in drawn_objects if o.get("content") == "member_name")
        intro_obj = next(o for o in drawn_objects if o.get("content") == "certificate_intro")

        assert title_obj["color"] == expected_color
        assert member_obj["color"] == expected_color
        # Non-title/member object retains its original color
        assert intro_obj["color"] == [107, 107, 107, 1]


@pytest.mark.django_db
def test_certificate_renderer_preserves_custom_color_and_does_not_mutate(event):
    from unittest.mock import MagicMock

    from teamshifts.pdf import CertificateRenderer, default_layout, hex_to_rgba

    with scope(event=event, organizer=event.organizer):
        event_color = "#c0392b"
        ctx = {"_event_color": event_color}
        layout = default_layout()

        # Set custom color for certificate_title in the input layout
        custom_green = [0, 255, 0, 1]
        for obj in layout:
            if obj.get("content") == "certificate_title":
                obj["color"] = list(custom_green)

        # Snapshot of input layout to test immutability
        original_title_color = list(next(o for o in layout if o.get("content") == "certificate_title")["color"])
        original_member_color = list(next(o for o in layout if o.get("content") == "member_name")["color"])

        renderer = CertificateRenderer(event, layout, None, ctx)
        drawn_objects = []
        renderer._draw_textarea = MagicMock(side_effect=lambda c, op, order, o: drawn_objects.append(o))
        renderer._draw_imagearea = MagicMock()
        renderer._draw_poweredby = MagicMock()

        canvas = MagicMock()
        renderer.draw_page(canvas, show_page=False)

        title_obj = next(o for o in drawn_objects if o.get("content") == "certificate_title")
        member_obj = next(o for o in drawn_objects if o.get("content") == "member_name")
        intro_obj = next(o for o in drawn_objects if o.get("content") == "certificate_intro")

        # Custom color on certificate_title is preserved; default NAVY member_name receives event color
        assert title_obj["color"] == custom_green
        assert member_obj["color"] == hex_to_rgba(event_color)
        # Non-title/member object retains its original color
        assert intro_obj["color"] == [107, 107, 107, 1]

        # Input layout dicts must NOT have been mutated in place
        assert next(o for o in layout if o.get("content") == "certificate_title")["color"] == original_title_color
        assert next(o for o in layout if o.get("content") == "member_name")["color"] == original_member_color


@pytest.mark.django_db
def test_get_certificate_settings_preserves_custom_layout(event):
    import json

    from teamshifts.pdf import default_layout, layout_is_initial_overlay
    from teamshifts.services.certificates import get_certificate_settings

    with scope(event=event):
        layout = default_layout()
        custom_color = [39, 174, 96, 1]
        for obj in layout:
            if obj.get("content") == "certificate_title":
                obj["color"] = custom_color
        layout_json = json.dumps(layout)

        assert layout_is_initial_overlay(layout_json) is False

        settings = get_certificate_settings(event)
        settings.layout = layout_json
        settings.save(update_fields=["layout"])

        # Subsequent retrieval must NOT overwrite with default_layout
        refreshed_settings = get_certificate_settings(event)
        parsed_layout = json.loads(refreshed_settings.layout)
        title_obj = next(o for o in parsed_layout if o.get("content") == "certificate_title")
        assert title_obj["color"] == custom_color


def test_layout_is_initial_overlay_detects_legacy_body_lines():
    import json

    from teamshifts.pdf import layout_is_initial_overlay

    legacy_layout = json.dumps(
        [
            {"type": "textarea", "content": "certificate_intro"},
            {"type": "textarea", "content": "certificate_title"},
            {"type": "textarea", "content": "member_name"},
            {"type": "textarea", "content": "certificate_body_line1"},
            {"type": "textarea", "content": "certificate_body_line2"},
            {"type": "textarea", "content": "issued_date"},
        ]
    )
    assert layout_is_initial_overlay(legacy_layout) is True


def test_layout_is_initial_overlay_preserves_custom_member_name_color():
    import json

    from teamshifts.pdf import default_layout, layout_is_initial_overlay

    layout = default_layout()
    for obj in layout:
        if obj.get("content") == "member_name":
            obj["color"] = [255, 0, 0, 1]
    layout_json = json.dumps(layout)
    assert layout_is_initial_overlay(layout_json) is False


def test_layout_is_initial_overlay_preserves_legacy_variant_with_custom_color():
    import json

    from teamshifts.pdf import layout_is_initial_overlay

    legacy_layout = json.dumps(
        [
            {"type": "textarea", "content": "certificate_intro"},
            {"type": "textarea", "content": "certificate_title", "color": [0, 128, 0, 1]},
            {"type": "textarea", "content": "member_name"},
            {"type": "textarea", "content": "certificate_body"},
            {"type": "textarea", "content": "issued_date"},
        ]
    )
    assert layout_is_initial_overlay(legacy_layout) is False


def test_default_layout():
    from teamshifts.pdf import NAVY, default_layout

    layout = default_layout()
    title = next(o for o in layout if o.get("content") == "certificate_title")
    member = next(o for o in layout if o.get("content") == "member_name")
    assert title["color"] == NAVY
    assert member["color"] == NAVY


@pytest.mark.django_db
def test_certificate_editor_view_get_current_layout(event, rf):
    from teamshifts.certificate_views import CertificateEditorView
    from teamshifts.pdf import NAVY

    with scope(event=event, organizer=event.organizer):
        event.settings.set("primary_color", "#e67e22")

        view = CertificateEditorView()
        request = rf.get("/")
        request.event = event
        request.organizer = event.organizer
        view.request = request

        # Unconfigured layout should return raw default layout (NAVY), not injected event color
        layout = view.get_current_layout()
        title = next(o for o in layout if o.get("content") == "certificate_title")
        member = next(o for o in layout if o.get("content") == "member_name")
        assert title["color"] == NAVY
        assert member["color"] == NAVY

        # Custom configured layout should be respected
        custom_color = [100, 200, 50, 1]
        settings = view.certificate_settings
        import json

        settings.layout = json.dumps(
            [
                {
                    "type": "textarea",
                    "content": "certificate_title",
                    "color": custom_color,
                }
            ]
        )
        settings.save(update_fields=["layout"])

        custom_layout = view.get_current_layout()
        custom_title = next(o for o in custom_layout if o.get("content") == "certificate_title")
        assert custom_title["color"] == custom_color

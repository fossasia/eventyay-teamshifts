import pytest


def test_plugin_version():
    """Verify the package exposes its expected version string."""
    import teamshifts

    assert teamshifts.__version__ == "0.1.0"


def test_appconfig_meta():
    """Verify the Django AppConfig and EventyayPluginMeta attributes."""
    try:
        from teamshifts.apps import TeamShiftsApp
    except (RuntimeError, ModuleNotFoundError):
        import pytest

        pytest.skip("eventyay or Django not installed, skipping AppConfig test")

    assert TeamShiftsApp.name == "teamshifts"
    meta = TeamShiftsApp.EventyayPluginMeta
    assert meta.author == "FOSSASIA"
    assert meta.visible is True


def test_plugin_entry_point():
    """Verify the plugin is registered under the pretix.plugin entry point group."""
    from importlib.metadata import entry_points

    eps = entry_points(group="pretix.plugin")
    names = [ep.name for ep in eps]
    assert "teamshifts" in names


def test_base_template_sidebar_arrows_use_metismenu():
    """Verify sidebar arrow links omit Bootstrap collapse attributes and include shift_duplicate in Setup/Shifts."""
    from html.parser import HTMLParser
    from pathlib import Path

    template_path = Path(__file__).resolve().parent.parent / "teamshifts" / "templates" / "teamshifts" / "base.html"
    content = template_path.read_text(encoding="utf-8")

    class SidebarArrowParser(HTMLParser):
        """HTML parser that collects attributes of sidebar arrow anchor tags."""

        def __init__(self):
            """Initialize the parser and storage for arrow tag attributes."""
            super().__init__()
            self.arrow_attrs = []

        def handle_starttag(self, tag, attrs):
            """Record attributes for anchor tags carrying the 'arrow' CSS class."""
            attr_dict = dict(attrs)
            classes = attr_dict.get("class", "").split()
            if tag == "a" and "arrow" in classes:
                self.arrow_attrs.append(attr_dict)

    parser = SidebarArrowParser()
    parser.feed(content)

    assert len(parser.arrow_attrs) == 5
    for attrs in parser.arrow_attrs:
        assert "data-toggle" not in attrs
        assert "data-target" not in attrs
    assert content.count("url_name == 'shift_duplicate'") == 4


@pytest.mark.django_db
def test_sidebar_expanded_and_collapsed_states(request):
    """Verify rendered sidebar sections expand and mark active links on Setup and Shift Duplicate routes."""
    try:
        from datetime import timedelta

        from django.urls import reverse
        from django.utils.timezone import now
        from django_scopes import scope
        from eventyay.base.models import Team

        from teamshifts.models import Shift, ShiftLocation
    except (RuntimeError, ModuleNotFoundError):
        pytest.skip("eventyay or Django not installed, skipping sidebar view test")

    client = request.getfixturevalue("client")
    event = request.getfixturevalue("event")
    user = request.getfixturevalue("user")
    settings = request.getfixturevalue("settings")
    settings.SITE_URL = "https://testserver"

    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
        location = ShiftLocation.objects.create(event=event, name="Main Hall")
        start = now() + timedelta(days=1)
        shift = Shift.objects.create(
            event=event,
            name="Setup",
            location=location,
            start_time=start,
            end_time=start + timedelta(hours=2),
        )
    client.force_login(user)

    dashboard_url = reverse(
        "plugins:teamshifts:dashboard",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = client.get(dashboard_url)
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'class="arrow" href="#" data-toggle="collapse"' not in html
    assert 'id="collapse-setup"' in html

    roles_url = reverse(
        "plugins:teamshifts:roles",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    roles_response = client.get(roles_url)
    assert roles_response.status_code == 200
    roles_html = roles_response.content.decode("utf-8")
    assert 'class="nav nav-second-level in"\n            id="collapse-setup"' in roles_html

    duplicate_url = reverse(
        "plugins:teamshifts:shift_duplicate",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, "pk": shift.pk},
    )
    duplicate_response = client.get(duplicate_url)
    assert duplicate_response.status_code == 200
    duplicate_html = duplicate_response.content.decode("utf-8")
    assert 'class="nav nav-second-level in"\n            id="collapse-setup"' in duplicate_html
    shifts_url = reverse(
        "plugins:teamshifts:shifts",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    assert f'<a href="{shifts_url}"\n                   class="active">' in duplicate_html

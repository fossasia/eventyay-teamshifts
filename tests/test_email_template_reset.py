import pytest
from django.urls import reverse
from django_scopes import scope
from eventyay.base.models import Team
from i18nfield.strings import LazyI18nString

from teamshifts.mail.default_templates import get_default_template
from teamshifts.models import CallForTeamMembers, EmailTemplateRoles

ROLE = EmailTemplateRoles.SHIFT_REMINDER


@pytest.fixture(autouse=True)
def site_url(settings):
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True)


@pytest.fixture
def organizer_user(event, user):
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, name="Orga", can_change_event_settings=True, all_events=True)
        team.members.add(user)
    return user


@pytest.fixture
def customised(event, cfm):
    with scope(event=event):
        template = cfm.get_mail_template(ROLE)
        template.subject = LazyI18nString({"en": "My own subject"})
        template.body = LazyI18nString({"en": "My own body"})
        template.send_automatically = False
        template.save()
    return template


def _url(name, event, **extra):
    return reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, **extra})


def _panel(response, role):
    return next(panel for panel in response.context["panels"] if panel["role"] == role)


@pytest.mark.django_db
def test_reset_restores_default_text_and_keeps_other_settings(client, event, organizer_user, customised):
    client.force_login(organizer_user)

    response = client.post(_url("email_template_reset", event, role=ROLE))

    assert response.status_code == 302
    customised.refresh_from_db()
    default_subject, default_body = get_default_template(ROLE)
    assert str(customised.subject) == str(default_subject)
    assert str(customised.body) == str(default_body)
    assert customised.send_automatically is False
    assert _panel(client.get(_url("email_templates", event)), ROLE)["is_customised"] is False


@pytest.mark.django_db
def test_change_in_secondary_language_counts_as_customised(client, event, organizer_user, cfm):
    event.settings.locales = ["en", "de"]
    with scope(event=event):
        template = cfm.get_mail_template(ROLE)
        assert _panel_is_customised(client, event, organizer_user) is False
        body = dict(template.body.data)
        body["de"] = "Eigener Text"
        template.body = LazyI18nString(body)
        template.save()

    assert _panel_is_customised(client, event, organizer_user) is True

    client.post(_url("email_template_reset", event, role=ROLE))

    template.refresh_from_db()
    assert template.body.localize("de") != "Eigener Text"
    assert _panel_is_customised(client, event, organizer_user) is False


def _panel_is_customised(client, event, user):
    client.force_login(user)
    return _panel(client.get(_url("email_templates", event)), ROLE)["is_customised"]


@pytest.mark.django_db
def test_reset_asks_for_confirmation_first(client, event, organizer_user, customised):
    client.force_login(organizer_user)

    response = client.get(_url("email_template_reset", event, role=ROLE))

    assert response.status_code == 200
    assert response.context["role_label"] == ROLE.label
    customised.refresh_from_db()
    assert str(customised.subject) == "My own subject"


@pytest.mark.django_db
def test_reset_button_only_on_customised_templates(client, event, organizer_user, customised):
    client.force_login(organizer_user)

    content = client.get(_url("email_templates", event)).content.decode()

    assert _url("email_template_reset", event, role=ROLE) in content
    assert _url("email_template_reset", event, role=EmailTemplateRoles.SHIFT_UNASSIGNED_BY_ORGANIZER) not in content


@pytest.mark.django_db
def test_reset_unknown_role_is_404(client, event, organizer_user, cfm):
    client.force_login(organizer_user)

    assert client.post(_url("email_template_reset", event, role="teamshifts.not_a_role")).status_code == 404


@pytest.mark.django_db
def test_reset_needs_email_permission(client, event, django_user_model, customised):
    # On the organizer's team with event access, but no Team Shifts role.
    user = django_user_model.objects.create_user(email="orders@example.com", password="x")
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, name="Orders", all_events=True, can_view_orders=True)
        team.members.add(user)
    client.force_login(user)

    response = client.post(_url("email_template_reset", event, role=ROLE))

    assert response.status_code == 403
    customised.refresh_from_db()
    assert str(customised.subject) == "My own subject"

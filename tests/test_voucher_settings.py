from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django_scopes import scope, scopes_disabled
from eventyay.base.models import Team, Voucher

from teamshifts.models import ApplicationStatus, MemberVoucher, TeamMemberApplication, VolunteerVoucherSettings, VoucherStatus

VOUCHER_TAG = "volunteer-batch"


@pytest.fixture
def orga_client(client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_voucher_settings_renders_bulk_voucher_link(orga_client, event):
    url = reverse(
        "plugins:teamshifts:voucher_settings",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    response = orga_client.get(url)

    assert response.status_code == 200

    bulk_voucher_url = reverse(
        "control:event.vouchers.bulk",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    assert bulk_voucher_url in response.content.decode("utf-8")


def _settings_url(event):
    return reverse(
        "plugins:teamshifts:voucher_settings",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _members_url(event):
    return reverse(
        "plugins:teamshifts:members",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _make_batch(event, count=2, redeemed=0):
    with scopes_disabled():
        return [Voucher.objects.create(event=event, tag=VOUCHER_TAG, max_usages=1, redeemed=redeemed) for _ in range(count)]


def _make_settings(event, enabled=True, voucher_tag=VOUCHER_TAG):
    with scopes_disabled():
        return VolunteerVoucherSettings.objects.create(event=event, enabled=enabled, voucher_tag=voucher_tag)


class _ElementsById(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.elements[attrs["id"]] = attrs


def _elements(response):
    parser = _ElementsById()
    parser.feed(response.content.decode("utf-8"))
    return parser.elements


def _assert_send_button_disabled(elements):
    button = elements["send-vouchers-btn"]
    assert "disabled" in button["class"].split()
    assert "href" not in button
    assert button["aria-disabled"] == "true"


@pytest.mark.django_db
def test_send_vouchers_button_enabled_when_ready(orga_client, event):
    _make_batch(event)
    _make_settings(event)

    response = orga_client.get(_settings_url(event))

    assert response.status_code == 200
    assert response.context["send_vouchers_blocker"] is None
    send_url = f"{_members_url(event)}?voucher=not_sent"
    assert response.context["send_vouchers_url"] == send_url
    elements = _elements(response)
    button = elements["send-vouchers-btn"]
    assert button["href"] == send_url
    assert "disabled" not in button["class"].split()
    assert "aria-disabled" not in button
    assert "hidden" in elements["send-vouchers-hint"]
    assert "hidden" in elements["send-vouchers-unsaved-hint"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("enabled", "voucher_tag", "redeemed", "message"),
    [
        (False, VOUCHER_TAG, 0, "Enable volunteer vouchers and save to start sending."),
        (True, "", 0, "Select a voucher batch and save to start sending."),
        (True, VOUCHER_TAG, 1, "All codes in this batch have been used. Add more codes in Tickets → Vouchers."),
    ],
)
def test_send_vouchers_button_disabled_with_reason(orga_client, event, enabled, voucher_tag, redeemed, message):
    _make_batch(event, redeemed=redeemed)
    _make_settings(event, enabled=enabled, voucher_tag=voucher_tag)

    response = orga_client.get(_settings_url(event))

    assert response.status_code == 200
    assert str(response.context["send_vouchers_blocker"]) == message
    elements = _elements(response)
    _assert_send_button_disabled(elements)
    assert "hidden" not in elements["send-vouchers-hint"]
    assert message in response.content.decode("utf-8")


@pytest.mark.django_db
def test_send_vouchers_button_disabled_without_settings(orga_client, event):
    response = orga_client.get(_settings_url(event))

    assert str(response.context["send_vouchers_blocker"]) == "Enable volunteer vouchers and save to start sending."
    elements = _elements(response)
    _assert_send_button_disabled(elements)
    assert "hidden" not in elements["send-vouchers-hint"]


@pytest.mark.django_db
def test_send_vouchers_button_disabled_after_invalid_post(orga_client, event):
    _make_batch(event)
    _make_settings(event)

    # Ready settings are saved, but the rejected POST leaves the form out of sync with them.
    response = orga_client.post(_settings_url(event), {"enabled": "on", "voucher_tag": ""})

    assert response.status_code == 200
    assert response.context["send_vouchers_blocker"] is None
    elements = _elements(response)
    _assert_send_button_disabled(elements)
    assert "hidden" not in elements["send-vouchers-unsaved-hint"]
    assert "hidden" in elements["send-vouchers-hint"]
    form = elements["voucher-settings-form"]
    assert form["data-saved-enabled"] == "true"
    assert form["data-saved-voucher-tag"] == VOUCHER_TAG


@pytest.mark.django_db
def test_voucher_settings_description_links_members_page(orga_client, event):
    response = orga_client.get(_settings_url(event))

    assert f"<a href='{_members_url(event)}'>Members page</a>" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_save_message_links_to_send_vouchers_when_ready(orga_client, event):
    _make_batch(event)

    response = orga_client.post(_settings_url(event), {"enabled": "on", "voucher_tag": VOUCHER_TAG}, follow=True)

    message = str(list(response.context["messages"])[0])
    assert message == (f'Voucher settings saved. Next: <a href="{_members_url(event)}?voucher=not_sent">send vouchers to your team members</a>.')


@pytest.mark.django_db
def test_save_message_has_no_next_step_when_not_ready(orga_client, event):
    _make_batch(event)

    response = orga_client.post(_settings_url(event), {"voucher_tag": VOUCHER_TAG}, follow=True)

    assert [str(m) for m in response.context["messages"]] == ["Voucher settings saved."]


@pytest.mark.django_db
def test_members_voucher_filter(orga_client, event, django_user_model):
    sent_voucher, claimed_voucher = _make_batch(event)
    _make_settings(event)
    apps = {}
    with scopes_disabled():
        for name in ("not-sent", "sent", "claimed"):
            user = django_user_model.objects.create_user(email=f"{name}@example.com", password="x")
            apps[name] = TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED)
        MemberVoucher.objects.create(application=apps["sent"], voucher=sent_voucher, status=VoucherStatus.SENT)
        MemberVoucher.objects.create(application=apps["claimed"], voucher=claimed_voucher, status=VoucherStatus.SENT)
        claimed_voucher.redeemed = 1
        claimed_voucher.save(update_fields=["redeemed"])

    for status, expected in (("not_sent", "not-sent"), ("sent", "sent"), ("claimed", "claimed")):
        response = orga_client.get(_members_url(event), {"voucher": status})
        assert [m.pk for m in response.context["members"]] == [apps[expected].pk]

    response = orga_client.get(_members_url(event))
    assert len(response.context["members"]) == 3


@pytest.mark.django_db
def test_members_voucher_filter_ignored_when_vouchers_disabled(orga_client, event, django_user_model):
    _make_settings(event, enabled=False)
    with scopes_disabled():
        user = django_user_model.objects.create_user(email="member@example.com", password="x")
        app = TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED)

    response = orga_client.get(_members_url(event), {"voucher": "sent"})

    assert [m.pk for m in response.context["members"]] == [app.pk]
    assert "name='voucher'" not in response.content.decode("utf-8")

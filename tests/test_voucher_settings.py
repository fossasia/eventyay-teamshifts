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


@pytest.mark.django_db
def test_send_vouchers_button_enabled_when_ready(orga_client, event):
    _make_batch(event)
    _make_settings(event)

    response = orga_client.get(_settings_url(event))

    assert response.status_code == 200
    assert response.context["send_vouchers_blocker"] is None
    send_url = f"{_members_url(event)}?voucher=not_sent"
    assert response.context["send_vouchers_url"] == send_url
    assert f"href='{send_url}'" in response.content.decode("utf-8")


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
    content = response.content.decode("utf-8")
    assert "btn btn-success disabled" in content
    assert message in content


@pytest.mark.django_db
def test_send_vouchers_button_disabled_without_settings(orga_client, event):
    response = orga_client.get(_settings_url(event))

    assert str(response.context["send_vouchers_blocker"]) == "Enable volunteer vouchers and save to start sending."


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

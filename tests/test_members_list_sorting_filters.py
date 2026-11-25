from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scopes_disabled
from eventyay.base.models import Team, User, Voucher

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    MemberCertificate,
    MemberVoucher,
    Shift,
    ShiftAssignment,
    TeamMemberApplication,
    TeamRole,
    VolunteerVoucherSettings,
    VoucherStatus,
)

VOUCHER_TAG = "volunteer-batch"


@pytest.fixture(autouse=True)
def site_url(settings):
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def orga_user(event, user):
    with scopes_disabled():
        team = Team.objects.create(organizer=event.organizer, name="Orga", can_change_event_settings=True, all_events=True)
        team.members.add(user)
    return user


def _members_url(event):
    return reverse("plugins:teamshifts:members", kwargs={"organizer": event.organizer.slug, "event": event.slug})


def _make_member(event, email, fullname, arrived=False):
    user = User.objects.create_user(email=email, password="x", fullname=fullname)
    with scopes_disabled():
        return TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED, arrived=arrived)


def _assign(event, application, role, hours, checked_in=False):
    start = now() + timedelta(days=1)
    with scopes_disabled():
        shift = Shift.objects.create(event=event, name="Shift", start_time=start, end_time=start + timedelta(hours=hours))
        ShiftAssignment.objects.create(shift=shift, team_member=application.user, role=role, started_at=start if checked_in else None)


def _give_voucher(event, application, status, redeemed=0):
    with scopes_disabled():
        voucher = Voucher.objects.create(event=event, tag=VOUCHER_TAG, max_usages=1, redeemed=redeemed)
        MemberVoucher.objects.create(event=event, application=application, voucher=voucher, status=status)


@pytest.fixture
def members(event):
    """alice: 1 shift of 6h checked in, Registration, claimed voucher, downloaded certificate.
    bob: nothing. carol: 2 shifts of 1h, none checked in, Exhibition, sent voucher, generated certificate, arrived.

    Created carol first and with emails that do not follow the name order, and with shift counts that do not follow the hours order,
    so that a sort on the wrong column or a fall back to creation order cannot pass by accident.
    """
    with scopes_disabled():
        registration = TeamRole.objects.create(event=event, name="Registration")
        exhibition = TeamRole.objects.create(event=event, name="Exhibition")
        VolunteerVoucherSettings.objects.create(event=event, enabled=True, voucher_tag=VOUCHER_TAG)
    carol = _make_member(event, "b-carol@example.com", "Carol", arrived=True)
    alice = _make_member(event, "c-alice@example.com", "Alice")
    bob = _make_member(event, "a-bob@example.com", "bob")
    _assign(event, alice, registration, 6, checked_in=True)
    _assign(event, carol, exhibition, 1)
    _assign(event, carol, exhibition, 1)
    _give_voucher(event, alice, VoucherStatus.SENT, redeemed=1)
    _give_voucher(event, carol, VoucherStatus.SENT)
    with scopes_disabled():
        MemberCertificate.objects.create(application=alice, file="alice.pdf", notified_at=now(), downloaded_at=now())
        MemberCertificate.objects.create(application=carol, file="carol.pdf")
    return {"alice": alice, "bob": bob, "carol": carol}


def _names(response):
    return [member.user.fullname for member in response.context["members"]]


def _get(client, event, orga_user, **params):
    client.force_login(orga_user)
    return client.get(_members_url(event), params)


@pytest.mark.django_db
def test_default_order_is_by_name_ignoring_case(client, event, orga_user, members):
    response = _get(client, event, orga_user)
    assert _names(response) == ["Alice", "bob", "Carol"]
    assert response.context["current_ordering"] == "name_sort"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("name_sort", ["Alice", "bob", "Carol"]),
        ("-name_sort", ["Carol", "bob", "Alice"]),
        ("user__email", ["bob", "Carol", "Alice"]),
        ("-user__email", ["Alice", "Carol", "bob"]),
        ("role_sort", ["bob", "Carol", "Alice"]),
        ("-role_sort", ["Alice", "Carol", "bob"]),
        ("shifts_assigned", ["bob", "Alice", "Carol"]),
        ("-shifts_assigned", ["Carol", "Alice", "bob"]),
        ("hours_sort", ["bob", "Carol", "Alice"]),
        ("-hours_sort", ["Alice", "Carol", "bob"]),
        ("shifts_checked_in", ["bob", "Carol", "Alice"]),
        ("-shifts_checked_in", ["Alice", "bob", "Carol"]),
        ("certificate_sort", ["bob", "Carol", "Alice"]),
        ("-certificate_sort", ["Alice", "Carol", "bob"]),
        ("voucher_sort", ["bob", "Carol", "Alice"]),
        ("-voucher_sort", ["Alice", "Carol", "bob"]),
        ("arrived", ["Alice", "bob", "Carol"]),
        ("-arrived", ["Carol", "Alice", "bob"]),
    ],
)
def test_sort_each_column_both_directions(client, event, orga_user, members, sort, expected):
    response = _get(client, event, orga_user, sort=sort)
    assert _names(response) == expected
    assert response.context["current_ordering"] == sort


@pytest.mark.django_db
def test_role_sort_ignores_case_of_role_names(client, event, orga_user):
    """A member's role sorts by its lowercase name, also when they have several roles with mixed case.

    pat has "b-role" and "Z-role", so the lowest role is "b-role" ignoring case. A case-sensitive sort would
    pick "Z-role" and put pat before quin, whose only role is "a-role".
    """
    with scopes_disabled():
        roles = {name: TeamRole.objects.create(event=event, name=name) for name in ("b-role", "Z-role", "a-role")}
    pat = _make_member(event, "pat@example.com", "pat")
    quin = _make_member(event, "quin@example.com", "quin")
    _assign(event, pat, roles["b-role"], 1)
    _assign(event, pat, roles["Z-role"], 1)
    _assign(event, quin, roles["a-role"], 1)

    assert _names(_get(client, event, orga_user, sort="role_sort")) == ["quin", "pat"]
    assert _names(_get(client, event, orga_user, sort="-role_sort")) == ["pat", "quin"]


@pytest.mark.django_db
def test_every_column_header_is_sortable(client, event, orga_user, members):
    content = _get(client, event, orga_user).content.decode()
    for field in ("name_sort", "user__email", "role_sort", "shifts_assigned", "shifts_checked_in", "hours_sort", "voucher_sort", "certificate_sort", "arrived"):
        assert f"sort={field}" in content, field
        assert f"sort=-{field}" in content, field


@pytest.mark.django_db
def test_unknown_sort_falls_back_to_default(client, event, orga_user, members):
    response = _get(client, event, orga_user, sort="user__password")
    assert _names(response) == ["Alice", "bob", "Carol"]
    assert response.context["current_ordering"] == "name_sort"


@pytest.mark.django_db
def test_blank_full_name_sorts_by_the_email_the_table_shows(client, event, orga_user, members):
    _make_member(event, "bcd@example.com", "")

    def shown(response):
        return [member.user.fullname or member.user.email for member in response.context["members"]]

    assert shown(_get(client, event, orga_user)) == ["Alice", "bcd@example.com", "bob", "Carol"]
    assert shown(_get(client, event, orga_user, sort="-name_sort")) == ["Carol", "bob", "bcd@example.com", "Alice"]


@pytest.mark.django_db
def test_email_sort_ignored_without_email_permission(client, event, members, django_user_model):
    viewer = django_user_model.objects.create_user(email="viewer@example.com", password="x")
    with scopes_disabled():
        team = Team.objects.create(organizer=event.organizer, name="Lead", all_events=True, teamshifts_role="lead", hide_teamshifts_emails=True)
        team.members.add(viewer)
    response = _get(client, event, viewer, sort="-user__email")
    assert response.status_code == 200
    assert not response.context["can_view_email"]
    assert response.context["current_ordering"] == "name_sort"
    assert _names(response) == ["Alice", "bob", "Carol"]


@pytest.mark.django_db
def test_voucher_sort_ignored_when_vouchers_disabled(client, event, orga_user, members):
    with scopes_disabled():
        VolunteerVoucherSettings.objects.filter(event=event).update(enabled=False)
    response = _get(client, event, orga_user, sort="-voucher_sort")
    assert response.context["current_ordering"] == "name_sort"
    assert _names(response) == ["Alice", "bob", "Carol"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("hours", "expected"),
    [("some", ["Alice", "Carol"]), ("none", ["bob"]), ("", ["Alice", "bob", "Carol"]), ("bogus", ["Alice", "bob", "Carol"])],
)
def test_hours_filter(client, event, orga_user, members, hours, expected):
    response = _get(client, event, orga_user, hours=hours)
    assert _names(response) == expected


@pytest.mark.django_db
def test_voucher_filter_not_sent_includes_members_without_voucher(client, event, orga_user, members):
    response = _get(client, event, orga_user, voucher="not_sent")
    assert _names(response) == ["bob"]


@pytest.mark.django_db
def test_voucher_filter_claimed_and_sent(client, event, orga_user, members):
    assert _names(_get(client, event, orga_user, voucher="claimed")) == ["Alice"]
    assert _names(_get(client, event, orga_user, voucher="sent")) == ["Carol"]


@pytest.mark.django_db
def test_filters_combine_with_sort_and_total_matches(client, event, orga_user, members):
    response = _get(client, event, orga_user, hours="some", voucher="sent", sort="-shifts_assigned")
    assert _names(response) == ["Carol"]
    assert response.context["paginator"].count == 1

    response = _get(client, event, orga_user, hours="some", sort="-shifts_assigned", q="a")
    assert _names(response) == ["Carol", "Alice"]
    assert response.context["paginator"].count == 2


@pytest.mark.django_db
def test_filter_form_keeps_sort_and_page_links_keep_filters(client, event, orga_user, members):
    response = _get(client, event, orga_user, hours="some", sort="-hours_sort", page_size=1)
    content = response.content.decode()
    assert "name='sort' value='-hours_sort'" in content
    assert "hours=some" in content
    assert "sort=-hours_sort" in content
    assert "page=2" in content


@pytest.mark.django_db
def test_download_links_carry_hours_filter(client, event, orga_user, members):
    response = _get(client, event, orga_user, hours="none")
    assert "hours=none" in response.context["download_csv_url"]


@pytest.mark.django_db
def test_bulk_voucher_redirect_keeps_list_params(client, event, orga_user, members):
    client.force_login(orga_user)
    url = reverse("plugins:teamshifts:bulk_send_vouchers", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.post(f"{url}?hours=some&voucher=sent&sort=-hours_sort&q=a&page=2&evil=https://example.com")
    assert response.status_code == 302
    target = response.url
    assert target.startswith(_members_url(event) + "?")
    assert "hours=some" in target
    assert "voucher=sent" in target
    assert "sort=-hours_sort" in target
    assert "q=a" in target
    assert "evil" not in target
    assert "example.com" not in target
    # A page past the end of the shrunken list would be a 404, so the page is not carried over.
    assert "page=" not in target


@pytest.mark.django_db
def test_bulk_voucher_send_redirect_keeps_list_params(client, event, orga_user, members):
    with scopes_disabled():
        CallForTeamMembers.objects.create(event=event, active=True)
        for _ in range(2):
            Voucher.objects.create(event=event, tag=VOUCHER_TAG, max_usages=1, redeemed=0)
    client.force_login(orga_user)
    url = reverse("plugins:teamshifts:bulk_send_vouchers", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        response = client.post(f"{url}?hours=none&voucher=not_sent&sort=-name_sort&page=2", {"member_ids": [members["bob"].pk]})
    assert response.status_code == 302
    with scopes_disabled():
        assert MemberVoucher.objects.filter(application=members["bob"]).exists()
    target = response.url
    assert target.startswith(_members_url(event) + "?")
    assert "hours=none" in target
    assert "voucher=not_sent" in target
    assert "sort=-name_sort" in target
    assert "page=" not in target


@pytest.mark.django_db
def test_bulk_voucher_redirect_without_params_goes_to_plain_list(client, event, orga_user, members):
    client.force_login(orga_user)
    url = reverse("plugins:teamshifts:bulk_send_vouchers", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.post(url)
    assert response.status_code == 302
    assert response.url == _members_url(event)

from unittest.mock import patch

import pytest
from django_scopes import scopes_disabled
from eventyay.base.models import Voucher

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    MemberVoucher,
    TeamMemberApplication,
    VolunteerVoucherSettings,
    VoucherStatus,
)
from teamshifts.services.vouchers import allocate_and_send_vouchers

VOUCHER_TAG = "volunteer-batch"


@pytest.fixture
def cfm(event):
    with scopes_disabled():
        return CallForTeamMembers.objects.create(event=event, active=True)


@pytest.fixture
def voucher_settings(event):
    with scopes_disabled():
        return VolunteerVoucherSettings.objects.create(event=event, enabled=True, voucher_tag=VOUCHER_TAG)


@pytest.fixture
def voucher_batch(event):
    with scopes_disabled():
        return [Voucher.objects.create(event=event, tag=VOUCHER_TAG, max_usages=1, redeemed=0) for _ in range(3)]


def _make_member(event, email, django_user_model):
    u = django_user_model.objects.create_user(email=email, password="x")
    with scopes_disabled():
        app = TeamMemberApplication.objects.create(event=event, user=u, status=ApplicationStatus.ACCEPTED)
    return app


@pytest.mark.django_db
def test_allocate_unique_codes(event, cfm, voucher_settings, voucher_batch, django_user_model):
    app1 = _make_member(event, "a@example.com", django_user_model)
    app2 = _make_member(event, "b@example.com", django_user_model)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        result = allocate_and_send_vouchers(event, voucher_settings, [app1, app2])

    assert result["sent"] == 2
    with scopes_disabled():
        codes = list(MemberVoucher.objects.filter(application__in=[app1, app2]).values_list("voucher__code", flat=True))
    assert len(codes) == 2
    assert len(set(codes)) == 2


@pytest.mark.django_db
def test_resend_same_code_when_unclaimed(event, cfm, voucher_settings, voucher_batch, django_user_model):
    app = _make_member(event, "c@example.com", django_user_model)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        allocate_and_send_vouchers(event, voucher_settings, [app])

    with scopes_disabled():
        mv = MemberVoucher.objects.get(application=app)
        original_code = mv.voucher.code
        assert mv.status == VoucherStatus.SENT

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        result = allocate_and_send_vouchers(event, voucher_settings, [app])

    assert result["resent"] == 1
    assert result["sent"] == 0
    with scopes_disabled():
        mv.refresh_from_db()
        assert mv.voucher.code == original_code


@pytest.mark.django_db
def test_skip_claimed_voucher(event, cfm, voucher_settings, voucher_batch, django_user_model):
    app = _make_member(event, "d@example.com", django_user_model)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        allocate_and_send_vouchers(event, voucher_settings, [app])

    with scopes_disabled():
        mv = MemberVoucher.objects.get(application=app)
        mv.voucher.redeemed = 1
        mv.voucher.save(update_fields=["redeemed"])

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        result = allocate_and_send_vouchers(event, voucher_settings, [app])

    assert result["skipped_claimed"] == 1
    assert result["sent"] == 0
    assert result["resent"] == 0


@pytest.mark.django_db
def test_empty_batch_guard(event, cfm, voucher_settings, django_user_model):
    app = _make_member(event, "e@example.com", django_user_model)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        result = allocate_and_send_vouchers(event, voucher_settings, [app])

    assert result["skipped_no_vouchers"] == 1
    assert result["sent"] == 0


@pytest.mark.django_db
def test_email_failure_keeps_not_sent(event, cfm, voucher_settings, voucher_batch, django_user_model):
    app = _make_member(event, "f@example.com", django_user_model)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=False):
        result = allocate_and_send_vouchers(event, voucher_settings, [app])

    assert result["sent"] == 0
    with scopes_disabled():
        mv = MemberVoucher.objects.get(application=app)
        assert mv.status == VoucherStatus.NOT_SENT
        assert mv.sent_at is None


@pytest.mark.django_db
def test_skipped_no_email_counted(event, cfm, voucher_settings, voucher_batch, django_user_model):
    app_with_email = _make_member(event, "g@example.com", django_user_model)
    u_no_email = django_user_model.objects.create_user(email="", password="x")
    with scopes_disabled():
        app_no_email = TeamMemberApplication.objects.create(event=event, user=u_no_email, status=ApplicationStatus.ACCEPTED)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True):
        result = allocate_and_send_vouchers(event, voucher_settings, [app_with_email, app_no_email])

    assert result["sent"] == 1
    assert result["skipped_no_email"] == 1

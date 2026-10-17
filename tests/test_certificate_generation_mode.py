from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import User

from teamshifts.forms import CertificateSettingsForm
from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    CertificateSettings,
    CertificateTrigger,
    MemberCertificate,
    TeamMemberApplication,
)
from teamshifts.services.certificates import (
    event_has_ended,
    events_awaiting_certificates,
    generate_pending_certificates,
    get_certificate_settings,
    maybe_auto_issue_certificate,
    member_qualifies,
    send_certificates,
)


@pytest.fixture(autouse=True)
def active_scope(event):
    with scope(event=event, organizer=event.organizer):
        yield


@pytest.fixture
def application(event):
    member = User.objects.create_user(email="member@example.com", password="secret", fullname="Jane Member")
    with scope(event=event):
        CallForTeamMembers.objects.get_or_create(event=event)
        return TeamMemberApplication.objects.create(event=event, user=member, status=ApplicationStatus.ACCEPTED, arrived=True)


def _set_event_dates(event, date_from, date_to):
    event.date_from = date_from
    event.date_to = date_to
    event.save(update_fields=["date_from", "date_to"])


@pytest.mark.django_db
def test_new_settings_default_to_manual(event):
    settings = get_certificate_settings(event)
    assert settings.trigger == CertificateTrigger.MANUAL
    assert settings.require_after_event_end is False


@pytest.mark.django_db
def test_untouched_event_does_not_auto_generate(event, application):
    assert maybe_auto_issue_certificate(application) is None
    with scope(event=event):
        assert not MemberCertificate.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_enabling_automatic_generation_does_not_generate_for_existing_members(event, application):
    settings = get_certificate_settings(event)
    form = CertificateSettingsForm(
        data={"require_arrived": "on", "min_shifts": "1", "match_mode": "all", "generate_automatically": "on"},
        instance=settings,
    )
    assert form.is_valid()
    form.save()
    with scope(event=event):
        assert not MemberCertificate.objects.filter(application=application).exists()
        assert CertificateSettings.objects.get(event=event).trigger == CertificateTrigger.AUTO


@pytest.mark.django_db
def test_automatic_generation_issues_certificate_when_enabled(event, application):
    settings = get_certificate_settings(event)
    settings.trigger = CertificateTrigger.AUTO
    settings.save(update_fields=["trigger"])
    with patch("teamshifts.services.certificates.transaction.on_commit"):
        certificate = maybe_auto_issue_certificate(application)
    assert certificate is not None


@pytest.mark.django_db
def test_after_event_end_condition_blocks_until_last_day_has_ended(event, application):
    settings = get_certificate_settings(event)
    settings.require_after_event_end = True
    settings.save(update_fields=["require_after_event_end"])
    with scope(event=event):
        _set_event_dates(event, now() - timedelta(days=1), now() + timedelta(hours=1))
        assert event_has_ended(event) is False
        assert member_qualifies(application, settings) is False

        _set_event_dates(event, now() - timedelta(days=5), now() - timedelta(days=3))
        assert event_has_ended(event) is True
        assert member_qualifies(application, settings) is True


@pytest.mark.django_db
def test_event_end_uses_event_timezone(event):
    event.settings.timezone = "Pacific/Auckland"
    with scope(event=event):
        local_now = now().astimezone(ZoneInfo("Pacific/Auckland"))
        late_today = local_now.replace(hour=23, minute=0)
        _set_event_dates(event, late_today - timedelta(days=1), late_today)
        assert event_has_ended(event) is False


@pytest.mark.django_db
def test_generate_pending_certificates_only_after_event_end_and_when_automatic(event, application):
    settings = get_certificate_settings(event)
    settings.trigger = CertificateTrigger.AUTO
    settings.require_after_event_end = True
    settings.save(update_fields=["trigger", "require_after_event_end"])

    with scope(event=event):
        _set_event_dates(event, now() - timedelta(days=1), now() + timedelta(hours=1))
    assert generate_pending_certificates(event) == 0

    with scope(event=event):
        _set_event_dates(event, now() - timedelta(days=5), now() - timedelta(days=3))
    assert event.pk in events_awaiting_certificates()
    with patch("teamshifts.services.certificates.transaction.on_commit"):
        assert generate_pending_certificates(event) == 1
        assert generate_pending_certificates(event) == 0

    settings.trigger = CertificateTrigger.MANUAL
    settings.save(update_fields=["trigger"])
    assert event.pk not in events_awaiting_certificates()


@pytest.mark.django_db
def test_send_certificates_skips_members_who_do_not_qualify(event, application):
    other = User.objects.create_user(email="other@example.com", password="secret", fullname="Other Member")
    with scope(event=event):
        not_arrived = TeamMemberApplication.objects.create(event=event, user=other, status=ApplicationStatus.ACCEPTED)
    with patch("teamshifts.services.certificates.transaction.on_commit"):
        result = send_certificates(event, [application, not_arrived])
    assert result == {"sent": 1, "not_qualified": 1, "no_email": 0}
    with scope(event=event):
        assert MemberCertificate.objects.filter(application=application).exists()
        assert not MemberCertificate.objects.filter(application=not_arrived).exists()


@pytest.mark.django_db
def test_send_certificates_resends_to_already_notified_member(event, application):
    with patch("teamshifts.services.certificates.transaction.on_commit") as on_commit:
        send_certificates(event, [application])
        send_certificates(event, [application])
    assert on_commit.call_count == 2

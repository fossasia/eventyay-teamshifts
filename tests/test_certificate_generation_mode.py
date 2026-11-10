import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Event, Team, User

from teamshifts.forms import CertificateSettingsForm
from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    CertificateSettings,
    CertificateStatus,
    CertificateTrigger,
    MemberCertificate,
    TeamMemberApplication,
)
from teamshifts.services.certificates import (
    event_has_ended,
    events_awaiting_certificates,
    generate_certificate,
    generate_certificates,
    generate_pending_certificates,
    get_certificate_settings,
    issue_certificate,
    issue_event_end_certificates_task,
    maybe_auto_issue_certificate,
    member_qualifies,
    send_certificates,
)


@pytest.fixture(autouse=True)
def active_scope(event):
    with scope(event=event, organizer=event.organizer):
        yield


@pytest.fixture(autouse=True)
def sent_emails():
    with (
        patch("teamshifts.services.certificates.transaction") as transaction,
        patch("teamshifts.services.certificates._send_certificate_email") as send_email,
    ):
        transaction.on_commit.side_effect = lambda callback: callback()
        yield send_email


@pytest.fixture
def orga_client(client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    team = Team.objects.create(organizer=event.organizer, name="Orga Team", can_change_event_settings=True, all_events=True)
    team.members.add(user)
    client.force_login(user)
    return client


def _member(event, name="member", arrived=True):
    user = User.objects.create_user(email=f"{name}@example.com", password="secret", fullname=name)
    CallForTeamMembers.objects.get_or_create(event=event)
    return TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED, arrived=arrived)


@pytest.fixture
def application(event):
    return _member(event)


def _configure(event, ended=None, **fields):
    settings = get_certificate_settings(event)
    for key, value in fields.items():
        setattr(settings, key, value)
    settings.save()
    if ended is not None:
        days = -5 if ended else 0
        event.date_from = now() + timedelta(days=days - 1)
        event.date_to = now() + timedelta(days=days + 1)
        event.save(update_fields=["date_from", "date_to"])
    return settings


def _url(event, name, **kwargs):
    return reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, **kwargs})


def _fail_for(application, error=None):
    real_generate = generate_certificate

    def generate(target, *args, **kwargs):
        if target.pk == application.pk:
            raise error or OSError("storage unavailable")
        return real_generate(target, *args, **kwargs)

    return patch("teamshifts.services.certificates.generate_certificate", side_effect=generate)


@pytest.mark.django_db
def test_new_and_untouched_events_stay_manual(event, application):
    settings = get_certificate_settings(event)
    assert settings.trigger == CertificateTrigger.MANUAL
    assert settings.require_arrived is True
    assert settings.require_after_event_end is False
    assert maybe_auto_issue_certificate(application) is None
    assert not MemberCertificate.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_enabling_automatic_generation_only_covers_later_qualifiers(event, application):
    form = CertificateSettingsForm(
        data={"require_arrived": "on", "min_shifts": "1", "match_mode": "all", "generate_automatically": "on"},
        instance=get_certificate_settings(event),
    )
    assert form.is_valid()
    form.save()
    assert CertificateSettings.objects.get(event=event).trigger == CertificateTrigger.AUTO
    assert not MemberCertificate.objects.filter(application=application).exists()

    assert maybe_auto_issue_certificate(application) is not None


@pytest.mark.django_db
def test_after_event_end_gate_uses_last_day_in_event_timezone(event, application):
    settings = _configure(event, ended=False, require_after_event_end=True)
    assert event_has_ended(event) is False
    assert member_qualifies(application, settings) is False

    _configure(event, ended=True)
    assert event_has_ended(event) is True
    assert member_qualifies(application, settings) is True

    Event.objects.filter(pk=event.pk).update(timezone="Pacific/Auckland")
    event = Event.objects.get(pk=event.pk)
    local_now = now().astimezone(event.tz)
    event.date_to = local_now.replace(hour=23, minute=59)
    event.date_from = event.date_to - timedelta(days=1)
    event.save(update_fields=["date_from", "date_to"])
    assert event_has_ended(event) is False


@pytest.mark.django_db
def test_after_event_end_alone_qualifies_every_accepted_member(event, orga_client):
    absent = _member(event, "absent", arrived=False)
    settings = _configure(event, ended=False, require_arrived=False, require_after_event_end=True)
    assert member_qualifies(absent, settings) is False

    _configure(event, ended=True)
    assert member_qualifies(absent, settings) is True

    form = CertificateSettingsForm(data={"require_after_event_end": "on", "min_shifts": "1", "match_mode": "all"}, instance=settings)
    assert form.is_valid()

    response = orga_client.post(_url(event, "certificates"), {"min_shifts": "1", "match_mode": "all"})
    fieldsets = BeautifulSoup(response.content, "html.parser").find_all("fieldset")
    conditions = next(fieldset for fieldset in fieldsets if "Qualification conditions" in fieldset.legend.get_text())
    assert "Select at least one qualification condition." in conditions.get_text()
    assert "Select at least one qualification condition." not in fieldsets[0].get_text()


@pytest.mark.django_db
def test_existing_certificate_is_not_revoked_before_event_end(event, application):
    generate_certificate(application)
    _configure(event, ended=False, trigger=CertificateTrigger.AUTO, require_after_event_end=True)

    assert maybe_auto_issue_certificate(application) is None
    assert MemberCertificate.objects.filter(application=application).exists()


@pytest.mark.django_db
def test_scheduled_issuing_after_event_end(event, application, sent_emails):
    _configure(
        event,
        ended=False,
        trigger=CertificateTrigger.AUTO,
        require_after_event_end=True,
        after_event_end_issuing_since=now() - timedelta(days=30),
    )
    assert generate_pending_certificates(event) == 0

    _configure(event, ended=True)
    assert event.pk in events_awaiting_certificates()
    issue_event_end_certificates_task(event.pk)
    assert MemberCertificate.objects.filter(application=application, notified_at__isnull=False).exists()
    assert event.pk not in events_awaiting_certificates()

    MemberCertificate.objects.filter(application=application).update(notified_at=None)
    assert event.pk in events_awaiting_certificates()
    assert generate_pending_certificates(event) == 1
    assert sent_emails.call_count == 2

    _configure(event, trigger=CertificateTrigger.MANUAL)
    MemberCertificate.objects.filter(application=application).update(notified_at=None)
    assert event.pk not in events_awaiting_certificates()


@pytest.mark.django_db
@pytest.mark.parametrize(("ended_before_enabling", "issued"), [(False, 1), (True, 0)])
def test_scheduled_issuing_is_not_retroactive(event, application, ended_before_enabling, issued):
    settings = _configure(event, ended=ended_before_enabling)
    form = CertificateSettingsForm(
        data={"require_arrived": "on", "require_after_event_end": "on", "min_shifts": "1", "match_mode": "all", "generate_automatically": "on"},
        instance=settings,
    )
    assert form.is_valid()
    settings = form.save()
    assert settings.after_event_end_issuing_since is not None
    if not ended_before_enabling:
        settings.after_event_end_issuing_since = now() - timedelta(days=30)
        settings.save(update_fields=["after_event_end_issuing_since"])
        _configure(event, ended=True)

    assert (event.pk in events_awaiting_certificates()) is bool(issued)
    assert generate_pending_certificates(event) == issued

    form = CertificateSettingsForm(data={"require_arrived": "on", "min_shifts": "1", "match_mode": "all"}, instance=settings)
    assert form.is_valid()
    assert form.save().after_event_end_issuing_since is None


@pytest.mark.django_db
def test_send_certificates_skips_non_qualifying_and_resends_with_a_rebuilt_pdf(event, application, sent_emails):
    not_arrived = _member(event, "absent", arrived=False)

    assert send_certificates(event, [application, not_arrived]) == {"sent": 1, "resent": 0, "not_qualified": 1, "no_email": 0, "failed": 0}
    certificate = MemberCertificate.objects.get(application=application)
    first_file = certificate.file.name
    MemberCertificate.objects.filter(pk=certificate.pk).update(downloaded_at=now())

    assert send_certificates(event, [application])["resent"] == 1
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.DOWNLOADED
    assert certificate.file.name != first_file
    assert not certificate.file.storage.exists(first_file)
    assert not MemberCertificate.objects.filter(application=not_arrived).exists()
    assert sent_emails.call_count == 2


@pytest.mark.django_db
def test_generate_then_send_in_two_steps(orga_client, event, application, sent_emails):
    member_client = Client()
    member_client.force_login(application.user)
    download_url = reverse("plugins:teamshifts:my_shifts_certificate_download", kwargs={"event_id": event.pk})

    orga_client.post(_url(event, "certificates_generate"))
    certificate = MemberCertificate.objects.get(application=application)
    assert certificate.status == CertificateStatus.GENERATED
    assert sent_emails.call_count == 0
    assert member_client.get(download_url).status_code == 404

    orga_client.post(_url(event, "certificates_send"))
    certificate.refresh_from_db()
    assert certificate.status == CertificateStatus.SENT
    assert sent_emails.call_count == 1
    assert member_client.get(download_url).status_code == 200

    file_name = certificate.file.name
    orga_client.post(_url(event, "certificates_generate"))
    orga_client.post(_url(event, "certificates_send"))
    certificate.refresh_from_db()
    # Generating again rebuilds the sent certificate, but "Send" only emails certificates that were never sent.
    assert certificate.status == CertificateStatus.DOWNLOADED
    assert certificate.file.name != file_name
    assert sent_emails.call_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize("bulk_action", ["send", "scheduled"])
def test_bulk_actions_continue_after_a_failure(event, application, bulk_action):
    other = _member(event, "other")
    _configure(
        event,
        ended=True,
        trigger=CertificateTrigger.AUTO,
        require_after_event_end=True,
        after_event_end_issuing_since=now() - timedelta(days=30),
    )

    with _fail_for(application):
        if bulk_action == "send":
            assert send_certificates(event, [application, other]) == {"sent": 1, "resent": 0, "not_qualified": 0, "no_email": 0, "failed": 1}
        else:
            assert generate_pending_certificates(event) == 1

    assert not MemberCertificate.objects.filter(application=application).exists()
    assert MemberCertificate.objects.filter(application=other).exists()


def _design(color):
    # A design that is not the stock layout, so the settings are not reset to the default one.
    return json.dumps([{"type": "textarea", "content": "member_name", "color": color}])


@pytest.fixture
def attached_pdfs(sent_emails):
    """The PDF each certificate email would attach, read at the moment it is sent.

    The PDF is a stand-in made from the layout alone, so a different design gives different bytes.
    """
    pdfs = []

    def record(_application, certificate):
        with certificate.file.open("rb") as handle:
            pdfs.append(handle.read())

    sent_emails.side_effect = record
    with patch("teamshifts.services.certificates.render_certificate_pdf", side_effect=lambda settings, context, *a, **k: settings.layout.encode()):
        yield pdfs


@pytest.mark.django_db
def test_resending_attaches_a_pdf_built_from_the_current_design(event, application, attached_pdfs):
    _configure(event, layout=_design("#112233"))
    send_certificates(event, [application])
    _configure(event, layout=_design("#445566"))

    assert send_certificates(event, [application])["resent"] == 1

    assert attached_pdfs == [_design("#112233").encode(), _design("#445566").encode()]


@pytest.mark.django_db
def test_generate_rebuilds_certificates_that_were_already_sent_without_emailing(event, application, attached_pdfs):
    _configure(event, layout=_design("#112233"))
    send_certificates(event, [application])
    certificate = MemberCertificate.objects.get(application=application)
    notified_at = certificate.notified_at
    _configure(event, layout=_design("#445566"))

    assert generate_certificates(event) == {"generated": 1, "failed": 0}

    certificate.refresh_from_db()
    with certificate.file.open("rb") as handle:
        assert handle.read() == _design("#445566").encode()
    assert certificate.notified_at == notified_at
    assert len(attached_pdfs) == 1


@pytest.mark.django_db
def test_a_failed_rebuild_stops_the_resend_and_is_reported(event, application, attached_pdfs):
    _configure(event, layout=_design("#112233"))
    send_certificates(event, [application])
    _configure(event, layout=_design("#445566"))

    with _fail_for(application):
        assert send_certificates(event, [application]) == {"sent": 0, "resent": 0, "not_qualified": 0, "no_email": 0, "failed": 1}

    assert len(attached_pdfs) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("error", [OSError("storage unavailable"), KeyError("left")])
def test_a_failed_rebuild_only_fails_that_member_whatever_the_error(event, application, attached_pdfs, error):
    other = _member(event, "other")
    _configure(event, layout=_design("#112233"))
    send_certificates(event, [application, other])
    _configure(event, layout=_design("#445566"))

    with _fail_for(application, error):
        result = send_certificates(event, [application, other])

    assert result == {"sent": 0, "resent": 1, "not_qualified": 0, "no_email": 0, "failed": 1}
    assert attached_pdfs[-1] == _design("#445566").encode()  # the other member still got the new design


@pytest.mark.django_db
def test_a_sent_certificate_whose_file_is_gone_is_rebuilt_and_resent(event, application, attached_pdfs):
    _configure(event, layout=_design("#112233"))
    send_certificates(event, [application])
    MemberCertificate.objects.filter(application=application).update(file="")

    assert send_certificates(event, [application])["resent"] == 1

    assert attached_pdfs[-1] == _design("#112233").encode()


@pytest.mark.django_db
def test_regenerating_leaves_the_notification_state_alone(application):
    certificate = generate_certificate(application)
    MemberCertificate.objects.filter(pk=certificate.pk).update(notified_at=now(), downloaded_at=now())
    real_save = MemberCertificate.save

    def save_after_a_resend_failed(self, *args, **kwargs):
        # Another job resets the notification between this job loading the certificate and saving it.
        MemberCertificate.objects.filter(pk=self.pk).update(notified_at=None)
        return real_save(self, *args, **kwargs)

    with patch.object(MemberCertificate, "save", autospec=True, side_effect=save_after_a_resend_failed):
        generate_certificate(application)

    refreshed = MemberCertificate.objects.get(pk=certificate.pk)
    assert refreshed.notified_at is None
    assert refreshed.downloaded_at is not None


@pytest.mark.django_db
def test_regenerating_saves_new_file_before_removing_old_one(application):
    old_name = generate_certificate(application).file.name
    certificate = generate_certificate(application)

    assert certificate.file.name != old_name
    assert not certificate.file.storage.exists(old_name)
    assert certificate.file.storage.exists(certificate.file.name)


def _actions(response):
    soup = BeautifulSoup(response.content, "html.parser")
    return (
        soup.find("button", attrs={"form": "certificates-generate-form"}),
        soup.find("button", attrs={"form": "certificates-send-form"}),
        soup.find("button", attrs={"value": "download_all"}),
        str(response.context["generation_hint"]),
        soup,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("arrived", "state", "enabled", "hint"),
    [
        (False, None, (False, False, False), "No member meets the qualification conditions yet."),
        (True, None, (True, False, False), "Generate certificates first."),
        (True, "generated", (True, True, True), "1 certificate is ready to send."),
        (True, "sent", (True, False, True), "The certificate has been sent."),
    ],
)
def test_settings_page_actions_and_hint(orga_client, event, arrived, state, enabled, hint):
    application = _member(event, arrived=arrived)
    if state == "generated":
        generate_certificate(application)
    elif state == "sent":
        issue_certificate(application)

    generate, send, download, generation_hint, _soup = _actions(orga_client.get(_url(event, "certificates")))

    assert [not button.has_attr("disabled") for button in (generate, send, download)] == list(enabled)
    assert generation_hint.startswith(hint)
    if state == "generated":
        assert "Generate certificates (1)" in generate.get_text(" ", strip=True)
        assert "Send certificates (1)" in send.get_text(" ", strip=True)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("trigger", "hint"),
    [
        (CertificateTrigger.MANUAL, "Certificates can be generated once the event has ended on"),
        (CertificateTrigger.AUTO, "Certificates will be generated and emailed automatically once the event has ended on"),
    ],
)
def test_settings_page_before_event_end(orga_client, event, application, trigger, hint):
    _configure(event, ended=False, trigger=trigger, require_after_event_end=True)

    generate, send, download, generation_hint, soup = _actions(orga_client.get(_url(event, "certificates")))

    assert all(button.has_attr("disabled") for button in (generate, send, download))
    assert generation_hint.startswith(hint)
    assert soup.find("a", href=_url(event, "members")) is not None
    assert soup.find("form", id="certificates-generate-form").has_attr("data-asynctask")


@pytest.mark.django_db
def test_members_certificate_column_and_filter(orga_client, event):
    _member(event, "pending")
    generate_certificate(_member(event, "generated"))
    issue_certificate(_member(event, "sent"))
    issue_certificate(_member(event, "downloaded"))
    MemberCertificate.objects.filter(application__user__fullname="downloaded").update(downloaded_at=now())

    soup = BeautifulSoup(orga_client.get(_url(event, "members")).content, "html.parser")
    column = [th.get_text(strip=True) for th in soup.select("thead th")].index("Certificate")
    cells = {row.select("td")[1].get_text(strip=True): row.select("td")[column] for row in soup.select("tbody tr")}

    assert cells["pending"].get_text(" ", strip=True) == str(CertificateStatus.NOT_GENERATED.label)
    assert cells["generated"].find(class_="label")["class"] == ["label", CertificateStatus.GENERATED.label_class]
    assert cells["sent"].find(class_="label")["class"] == ["label", CertificateStatus.SENT.label_class]
    assert cells["sent"].find(class_="ts-certificate-sent-date") is not None
    assert cells["downloaded"].find(class_="label")["class"] == ["label", CertificateStatus.DOWNLOADED.label_class]

    expected = {
        CertificateStatus.NOT_GENERATED: {"pending"},
        CertificateStatus.GENERATED: {"generated"},
        CertificateStatus.SENT: {"sent"},
        CertificateStatus.DOWNLOADED: {"downloaded"},
    }
    for status, names in expected.items():
        response = orga_client.get(_url(event, "members"), {"certificate": status})
        assert {member.user.fullname for member in response.context["members"]} == names
        assert f"certificate={status}" in response.context["download_csv_url"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["bulk_send", "automatic"])
def test_manual_and_automatic_sending_update_members_column(orga_client, event, path):
    application = _member(event, arrived=path == "bulk_send")
    if path == "bulk_send":
        orga_client.post(_url(event, "bulk_send_certificates"), {"member_ids": [application.pk]})
    else:
        _configure(event, trigger=CertificateTrigger.AUTO)
        orga_client.post(_url(event, "member_toggle_arrived", pk=application.pk))

    application.refresh_from_db()
    assert application.certificate_status == CertificateStatus.SENT

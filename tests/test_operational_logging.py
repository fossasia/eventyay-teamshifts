"""Privacy, permission-denial, retry, and fail-open tests for plugin operational logs."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
from django.core.exceptions import PermissionDenied
from eventyay.base.services.mail import SendMailException

from teamshifts import operational_log
from teamshifts.operational_log import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    log_operation,
    log_permission_denied,
    traced_job,
)
from teamshifts.permissions import teamshifts_permission_required
from teamshifts.services import reminders as reminder_service, vouchers as voucher_service
from teamshifts.tasks import send_queued_email


@pytest.fixture
def captured(monkeypatch):
    records = []

    def fake_log(level, action, extra=None):
        records.append((level, action, extra or {}))

    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", None)
    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", fake_log)
    return records


def test_drops_email_token_and_unknown_fields(captured):
    log_operation(
        "plugin.loaded",
        OUTCOME_SUCCESS,
        backend="teamshifts",
        email="person@example.com",
        token="secret-token",
        body="{raw}",
        event_id=12,
    )
    assert len(captured) == 1
    extra = captured[0][2]
    assert extra["backend"] == "teamshifts"
    assert extra["event_id"] == 12
    assert extra["outcome"] == "success"
    assert "email" not in extra
    assert "token" not in extra
    assert "body" not in extra
    blob = str(extra)
    assert "person@example.com" not in blob
    assert "secret-token" not in blob


def test_rejects_unsafe_action_and_backend(captured):
    log_operation("has space", OUTCOME_SUCCESS, backend="teamshifts")
    log_operation("connection.request", OUTCOME_FAILURE, backend="team shifts", error_code="not safe")

    assert len(captured) == 1
    level, action, extra = captured[0]
    assert level == logging.WARNING
    assert action == "connection.request"
    assert extra["outcome"] == "failure"
    assert "backend" not in extra
    assert "error_code" not in extra
    assert "team shifts" not in str(captured)


def test_host_emitter_failure_falls_through_to_local_logger(monkeypatch):
    records = []

    def broken_host(*args, **kwargs):
        raise RuntimeError("host logger broken")

    def fake_log(level, action, extra=None):
        records.append((level, action, extra or {}))

    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", broken_host)
    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", fake_log)

    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend="teamshifts")
    assert len(records) == 1
    assert records[0][1] == "plugin.loaded"
    assert records[0][2]["backend"] == "teamshifts"


def test_logging_failure_is_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", boom)
    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", boom)
    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend="teamshifts")


def test_permission_denied_skips_anonymous_and_logs_authenticated(captured, monkeypatch):
    @teamshifts_permission_required("can_teamshifts_create_shifts")
    def protected_view(request):
        return "ok"

    event = SimpleNamespace(pk=42)
    anon_request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=False, pk=None),
        event=event,
        organizer=SimpleNamespace(pk=1),
    )
    with pytest.raises(PermissionDenied):
        protected_view(anon_request)
    assert captured == []

    # Direct helper call with anonymous user is also a no-op.
    log_permission_denied(anon_request, "permission_denied")
    assert captured == []

    monkeypatch.setattr("teamshifts.permissions.has_teamshifts_permission", lambda *args, **kwargs: False)
    auth_request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=True, pk=99),
        event=event,
        organizer=SimpleNamespace(pk=1),
    )
    with pytest.raises(PermissionDenied):
        protected_view(auth_request)

    assert len(captured) == 1
    level, action, extra = captured[0]
    assert level == logging.WARNING
    assert action == "permission.denied"
    assert extra["outcome"] == "failure"
    assert extra["backend"] == "teamshifts"
    assert extra["error_code"] == "permission_denied"
    assert extra["event_id"] == 42
    assert extra["user_id"] == 99


def test_traced_job_logs_lifecycle_without_leaking_exception_text(captured):
    @traced_job("teamshifts.sample_job")
    def ok_task(event):
        return 7

    @traced_job("teamshifts.sample_job")
    def failing_task(event):
        raise ValueError("secret@example.com leaked in error")

    event = SimpleNamespace(pk=55)
    assert ok_task(event) == 7
    assert [(item[1], item[2]["outcome"], item[2].get("event_id")) for item in captured] == [
        ("job.start", "success", 55),
        ("job.finish", "success", 55),
    ]

    captured.clear()
    with pytest.raises(ValueError):
        failing_task(event)
    assert [(item[1], item[2]["outcome"], item[2].get("error_code"), item[2].get("event_id")) for item in captured] == [
        ("job.start", "success", None, 55),
        ("job.fail", "failure", "ValueError", 55),
    ]
    assert "secret@example.com" not in str(captured)


def _mock_queue_env(monkeypatch, *, is_draft=False, recipients=None):
    fake_event = SimpleNamespace(pk=11, settings=SimpleNamespace(locale="en"))
    fake_queue = MagicMock()
    fake_queue.pk = 88
    fake_queue.sent_at = None
    fake_queue.is_draft = is_draft
    fake_queue.send_after = None
    fake_queue.is_shift_summary = False
    fake_queue.recipient_group = ""
    fake_queue.role_filter_id = None
    fake_queue.user = None
    fake_queue.shift_id = None
    fake_queue.locale = "en"
    fake_queue.subject = "Subject"
    fake_queue.message = "Body"
    fake_queue.bcc = ""
    fake_queue.reply_to = ""
    fake_queue.mail_type = "custom"
    fake_queue.recipients.select_related.return_value.all.return_value = recipients or []
    fake_queue.recipients.filter.return_value.exists.return_value = True

    monkeypatch.setattr("teamshifts.tasks.Event.objects.get", lambda pk: fake_event)
    monkeypatch.setattr("teamshifts.tasks.scope", lambda **kwargs: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False))
    qs = MagicMock()
    qs.select_related.return_value.filter.return_value.first.return_value = fake_queue
    monkeypatch.setattr("teamshifts.tasks.TeamShiftsEmailQueue.objects", qs)
    return fake_event, fake_queue


def test_send_queued_email_draft_emits_terminal_finish(captured, monkeypatch):
    _mock_queue_env(monkeypatch, is_draft=True)
    send_queued_email.run(event_id=11, queue_id=88)

    assert [(item[1], item[2]["outcome"], item[2].get("event_id"), item[2].get("object_id")) for item in captured] == [
        ("job.start", "success", 11, 88),
        ("job.finish", "success", 11, 88),
    ]


def test_send_queued_email_retry_vs_max_retries_and_privacy_safe_error(captured, monkeypatch, caplog):
    recipient = MagicMock()
    recipient.pk = 501
    recipient.sent_at = None
    recipient.email = "alice@example.com"
    recipient.user = None

    _mock_queue_env(monkeypatch, recipients=[recipient])

    recipient_mgr = MagicMock()
    recipient_mgr.filter.return_value.update.return_value = 1
    monkeypatch.setattr(type(recipient), "objects", recipient_mgr, raising=False)
    monkeypatch.setattr("teamshifts.tasks.get_email_context", lambda **kwargs: {})
    monkeypatch.setattr(
        "teamshifts.tasks.mail",
        MagicMock(side_effect=SendMailException("550 5.1.1 <alice@example.com>: Recipient address rejected")),
    )

    with caplog.at_level(logging.ERROR, logger="teamshifts.tasks"):
        with patch.object(send_queued_email, "retry", side_effect=Retry()) as mock_retry:
            with pytest.raises(Retry):
                send_queued_email.run(event_id=11, queue_id=88)
        mock_retry.assert_called_once()

    actions = [item[1] for item in captured]
    assert actions == ["job.start", "mail.send", "job.retry"]
    assert "job.fail" not in actions
    assert "alice@example.com" not in str(captured)
    assert "alice@example.com" not in caplog.text
    assert "550 5.1.1" not in caplog.text

    captured.clear()
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="teamshifts.tasks"):
        with patch.object(send_queued_email, "retry", side_effect=MaxRetriesExceededError()) as mock_retry:
            send_queued_email.run(event_id=11, queue_id=88)
        mock_retry.assert_called_once()

    actions = [item[1] for item in captured]
    assert actions == ["job.start", "mail.send", "job.fail"]
    assert "job.retry" not in actions
    assert captured[-1][2]["error_code"] == "max_retries"
    assert "alice@example.com" not in str(captured)
    assert "alice@example.com" not in caplog.text


def test_voucher_and_reminder_error_logs_omit_email_and_smtp_text(captured, monkeypatch, caplog):
    event = SimpleNamespace(pk=7, slug="ev")
    user = SimpleNamespace(pk=33, email="bob@example.com", locale="en")
    voucher = SimpleNamespace(code="SECRET-VOUCHER-123")
    template = SimpleNamespace(subject="S", body="B")

    monkeypatch.setattr("teamshifts.services.vouchers.build_absolute_uri", lambda *a, **kw: "https://example.invalid/redeem")
    monkeypatch.setattr("teamshifts.services.vouchers.get_email_context", lambda **kw: {})
    monkeypatch.setattr(
        "teamshifts.services.vouchers.mail",
        MagicMock(side_effect=SendMailException("SMTP failed for bob@example.com with SECRET-VOUCHER-123")),
    )

    with caplog.at_level(logging.ERROR, logger="teamshifts.services.vouchers"):
        ok = voucher_service._send_voucher_email(event, user, voucher, template, "en")

    assert ok is False
    assert len(captured) == 1
    assert captured[0][1] == "mail.send"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["user_id"] == 33
    assert "bob@example.com" not in caplog.text
    assert "SECRET-VOUCHER-123" not in caplog.text
    assert "bob@example.com" not in str(captured)

    captured.clear()
    caplog.clear()

    day = SimpleNamespace()
    assignment = SimpleNamespace(shift=SimpleNamespace(start_time=10))
    monkeypatch.setattr("teamshifts.services.reminders.due_event_user_days", lambda cur: [(event, user, day)])
    monkeypatch.setattr("teamshifts.services.reminders.day_assignments", lambda e, u, d: [assignment])
    monkeypatch.setattr("teamshifts.services.reminders._signed_up_in_advance", lambda a: True)
    monkeypatch.setattr("teamshifts.services.reminders._get_reminder_template", lambda e: template)
    monkeypatch.setattr("teamshifts.services.reminders._claim_reminder", lambda e, u, d: SimpleNamespace(pk=1, event=e))
    monkeypatch.setattr(
        "teamshifts.services.reminders._send_reminder",
        MagicMock(side_effect=SendMailException("SMTP failed for bob@example.com")),
    )

    with caplog.at_level(logging.ERROR, logger="teamshifts.services.reminders"):
        sent = reminder_service.send_due_shift_reminders(current=5)

    assert sent == 0
    assert len(captured) == 1
    assert captured[0][1] == "mail.send"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["user_id"] == 33
    assert "bob@example.com" not in caplog.text
    assert "bob@example.com" not in str(captured)

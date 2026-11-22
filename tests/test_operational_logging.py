"""Privacy, permission-denial, retry, correlation, and fail-open tests for plugin operational logs."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
from django.core.exceptions import PermissionDenied
from eventyay.base.services.mail import SendMailException

from teamshifts import apps as teamshifts_apps, operational_log, signals as teamshifts_signals, views as teamshifts_views
from teamshifts.operational_log import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    log_operation,
    log_permission_denied,
    traced_job,
)
from teamshifts.permissions import teamshifts_permission_required
from teamshifts.services import checkin as checkin_service, reminders as reminder_service, vouchers as voucher_service
from teamshifts.tasks import send_queued_email


@pytest.fixture
def captured(monkeypatch):
    records = []

    def fake_log(level, action, extra=None):
        records.append((level, action, extra or {}))

    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", None)
    monkeypatch.setattr(operational_log, "_host_get_request_id", None)
    monkeypatch.setattr(operational_log, "_host_get_job_id", None)
    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", fake_log)
    return records


def test_drops_email_token_and_unknown_fields_and_keeps_status_duration_correlation(captured):
    log_operation(
        "plugin.loaded",
        OUTCOME_SUCCESS,
        backend="teamshifts",
        email="person@example.com",
        token="secret-token",
        body="{raw}",
        event_id=12,
        status=200,
        duration_ms=15,
        request_id="req-abc-123",
        job_id="job-xyz-456",
    )
    assert len(captured) == 1
    extra = captured[0][2]
    assert extra["backend"] == "teamshifts"
    assert extra["event_id"] == 12
    assert extra["status"] == 200
    assert extra["duration_ms"] == 15
    assert extra["request_id"] == "req-abc-123"
    assert extra["job_id"] == "job-xyz-456"
    assert extra["outcome"] == "success"
    assert "email" not in extra
    assert "token" not in extra
    assert "body" not in extra
    blob = str(extra)
    assert "person@example.com" not in blob
    assert "secret-token" not in blob

    captured.clear()
    log_operation("plugin.loaded", OUTCOME_SUCCESS, status=True, duration_ms=False, request_id="has space")
    assert "status" not in captured[0][2]
    assert "duration_ms" not in captured[0][2]
    assert "request_id" not in captured[0][2]


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
        request_id="req-perm-99",
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
    assert extra["request_id"] == "req-perm-99"


def test_traced_job_logs_lifecycle_correlation_and_duration(captured):
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
    assert captured[0][2]["job_id"] == captured[1][2]["job_id"]
    assert isinstance(captured[1][2]["duration_ms"], int)

    captured.clear()
    with pytest.raises(ValueError):
        failing_task(event)
    assert [(item[1], item[2]["outcome"], item[2].get("error_code"), item[2].get("event_id")) for item in captured] == [
        ("job.start", "success", None, 55),
        ("job.fail", "failure", "ValueError", 55),
    ]
    assert captured[0][2]["job_id"] == captured[1][2]["job_id"]
    assert isinstance(captured[1][2]["duration_ms"], int)
    assert "secret@example.com" not in str(captured)


def test_app_ready_logs_plugin_loaded_after_registration(captured, monkeypatch):
    order = []
    app_cfg = teamshifts_apps.TeamShiftsApp("teamshifts", teamshifts_apps)
    monkeypatch.setattr(teamshifts_apps.post_migrate, "connect", lambda *a, **kw: order.append("post_migrate"))
    monkeypatch.setattr(teamshifts_apps, "log_plugin_loaded", lambda backend: order.append(f"loaded:{backend}"))

    app_cfg.ready()
    assert order == ["post_migrate", "loaded:teamshifts"]

    def fail_connect(*a, **kw):
        raise RuntimeError("connect failed")

    monkeypatch.setattr(teamshifts_apps.post_migrate, "connect", fail_connect)
    with pytest.raises(RuntimeError, match="connect failed"):
        app_cfg.ready()
    assert len(captured) == 1
    assert captured[0][1] == "plugin.loaded"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["error_code"] == "RuntimeError"


def test_enqueue_failures_logged_and_reraised_in_signals_and_views(captured, monkeypatch):
    monkeypatch.setattr(teamshifts_views.send_queued_email, "delay", MagicMock(side_effect=RuntimeError("broker down")))
    with pytest.raises(RuntimeError, match="broker down"):
        teamshifts_views._enqueue_queued_email(11, 22)

    assert len(captured) == 1
    assert captured[0][1] == "job.enqueue"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["error_code"] == "dispatch_failed"
    assert captured[0][2]["event_id"] == 11
    assert captured[0][2]["object_id"] == 22

    captured.clear()
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.add.return_value = True
    monkeypatch.setattr("eventyay.common.signals.cache", fake_cache)
    monkeypatch.setattr(teamshifts_signals, "events_awaiting_certificates", lambda: [91])
    monkeypatch.setattr(
        teamshifts_signals.issue_event_end_certificates_task,
        "apply_async",
        MagicMock(side_effect=RuntimeError("celery down")),
    )
    with pytest.raises(RuntimeError, match="celery down"):
        teamshifts_signals.dispatch_event_end_certificates(sender=None)

    assert len(captured) == 1
    assert captured[0][1] == "job.enqueue"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["error_code"] == "enqueue_failed"
    assert captured[0][2]["event_id"] == 91
    assert captured[0][2]["job_name"] == "teamshifts.issue_event_end_certificates"

    captured.clear()
    monkeypatch.setattr(teamshifts_signals, "scopes_disabled", lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False))
    monkeypatch.setattr(
        teamshifts_signals.transaction,
        "atomic",
        lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False),
    )
    qs = MagicMock()
    qs.filter.return_value.select_for_update.return_value.order_by.return_value.values_list.return_value = [(303, 12)]
    monkeypatch.setattr(teamshifts_signals.TeamShiftsEmailQueue, "objects", qs)
    monkeypatch.setattr(teamshifts_signals.cache, "add", lambda *a, **kw: True)
    monkeypatch.setattr(teamshifts_signals.send_queued_email, "delay", MagicMock(side_effect=RuntimeError("broker offline")))

    with pytest.raises(RuntimeError, match="broker offline"):
        teamshifts_signals.dispatch_scheduled_emails(sender=None)

    assert len(captured) == 1
    assert captured[0][1] == "job.enqueue"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["error_code"] == "enqueue_failed"
    assert captured[0][2]["event_id"] == 12
    assert captured[0][2]["object_id"] == 303


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


def test_send_queued_email_draft_emits_terminal_finish_with_timing_and_job_id(captured, monkeypatch):
    _mock_queue_env(monkeypatch, is_draft=True)
    send_queued_email.run(event_id=11, queue_id=88)

    assert [(item[1], item[2]["outcome"], item[2].get("event_id"), item[2].get("object_id")) for item in captured] == [
        ("job.start", "success", 11, 88),
        ("job.finish", "success", 11, 88),
    ]
    assert captured[0][2]["job_id"] == captured[1][2]["job_id"]
    assert isinstance(captured[1][2]["duration_ms"], int)


def test_send_queued_email_retry_exhaustion_and_privacy_safe_error(captured, monkeypatch, caplog):
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
    assert len({item[2]["job_id"] for item in captured}) == 1
    assert isinstance(captured[1][2]["duration_ms"], int)
    assert isinstance(captured[2][2]["duration_ms"], int)
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
    assert captured[-1][2]["error_code"] == "max_retries"
    assert isinstance(captured[-1][2]["duration_ms"], int)

    captured.clear()
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="teamshifts.tasks"):
        with patch.object(send_queued_email, "retry", side_effect=SendMailException("Partial send: alice@example.com")) as mock_retry:
            with pytest.raises(SendMailException):
                send_queued_email.run(event_id=11, queue_id=88)
        mock_retry.assert_called_once()

    actions = [item[1] for item in captured]
    assert actions == ["job.start", "mail.send", "job.fail"]
    assert captured[-1][2]["error_code"] == "SendMailException"
    assert isinstance(captured[-1][2]["duration_ms"], int)
    assert "alice@example.com" not in str(captured)


def test_checkin_failure_and_multiple_application_paths(captured, monkeypatch, caplog):
    checkin = SimpleNamespace(
        pk=77,
        type="entry",
        list=SimpleNamespace(event_id=15, event=SimpleNamespace(pk=15)),
        position=SimpleNamespace(voucher_id=None, attendee_email="volunteer@example.com"),
    )
    monkeypatch.setattr(checkin_service, "scope", lambda **kw: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False))
    app_mgr = MagicMock()
    app_mgr.get.side_effect = checkin_service.TeamMemberApplication.MultipleObjectsReturned()
    monkeypatch.setattr(checkin_service.TeamMemberApplication, "objects", app_mgr)

    assert checkin_service.resolve_volunteer_application(checkin) is None
    assert len(captured) == 1
    assert captured[0][1] == "checkin.resolve"
    assert captured[0][2]["error_code"] == "multiple_applications"
    assert captured[0][2]["event_id"] == 15
    assert "volunteer@example.com" not in str(captured)

    captured.clear()
    monkeypatch.setattr(teamshifts_signals, "handle_volunteer_checkin", MagicMock(side_effect=RuntimeError("db error")))
    with caplog.at_level(logging.ERROR, logger="teamshifts.signals"):
        teamshifts_signals.handle_checkin_created(sender=None, checkin=checkin)

    assert len(captured) == 1
    assert captured[0][1] == "checkin.volunteer"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["error_code"] == "RuntimeError"
    assert captured[0][2]["event_id"] == 15
    assert captured[0][2]["object_id"] == 77


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

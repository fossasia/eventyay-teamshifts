"""Privacy-safe operational logs for this plugin.

Delegates to the host logger when it is importable. Otherwise uses
``eventyay.plugins``. Logging never raises into product code.

Emails, names, tokens, secrets, and request or response bodies are dropped.
Only opaque IDs, status codes, durations, correlation IDs, and safe error
codes are kept.
"""

from __future__ import annotations

import functools
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LOGGER = logging.getLogger("eventyay.plugins")
_request_id: ContextVar[str | None] = ContextVar("teamshifts_request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("teamshifts_job_id", default=None)

_INT_FIELDS = frozenset(
    {
        "event_id",
        "user_id",
        "status",
        "duration_ms",
        "retry_count",
        "recipient_count",
    }
)
_IDENTIFIER_FIELDS = frozenset(
    {
        "error_code",
        "backend",
        "job_name",
        "mail_type",
        "request_id",
        "job_id",
    }
)

try:
    from eventyay.base.operational_logging import (
        get_job_id as _host_get_job_id,
        get_request_id as _host_get_request_id,
        log_event as _HOST_LOG_EVENT,
    )
except Exception:
    _HOST_LOG_EVENT = None
    _host_get_request_id = None
    _host_get_job_id = None


def _safe_identifier(value):
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return None


def current_request_id() -> str | None:
    value = _request_id.get()
    if value is None and _host_get_request_id is not None:
        try:
            value = _host_get_request_id()
        except Exception:
            value = None
    return _safe_identifier(value)


def current_job_id() -> str | None:
    value = _job_id.get()
    if value is None and _host_get_job_id is not None:
        try:
            value = _host_get_job_id()
        except Exception:
            value = None
    return _safe_identifier(value)


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


@contextmanager
def job_correlation(job_id: str | None = None):
    """Bind a correlation job_id for the duration of a background task."""
    resolved = _safe_identifier(job_id) or current_job_id() or uuid.uuid4().hex
    token = _job_id.set(resolved)
    try:
        yield resolved
    finally:
        _job_id.reset(token)


def _clean(fields):
    extra = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key in _INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            extra[key] = value
            continue
        if key in _IDENTIFIER_FIELDS:
            cleaned = _safe_identifier(value if isinstance(value, str) else None)
            if cleaned is not None:
                extra[key] = cleaned
            continue
        if key == "object_id":
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                extra[key] = value
            else:
                cleaned = _safe_identifier(str(value))
                if cleaned is not None:
                    extra[key] = cleaned
    return extra


def log_operation(action, outcome, **fields):
    """Emit one allowlisted operational line. Failures are swallowed."""
    try:
        if not isinstance(action, str) or _safe_identifier(action) is None:
            return
        if outcome not in (OUTCOME_SUCCESS, OUTCOME_FAILURE):
            outcome = OUTCOME_FAILURE
        level = logging.WARNING if outcome == OUTCOME_FAILURE else logging.INFO
        payload = _clean(fields)
        if "request_id" not in payload:
            req_id = current_request_id()
            if req_id is not None:
                payload["request_id"] = req_id
        if "job_id" not in payload:
            job_id = current_job_id()
            if job_id is not None:
                payload["job_id"] = job_id
        if _HOST_LOG_EVENT is not None:
            try:
                _HOST_LOG_EVENT("plugins", action, outcome, level=level, **payload)
                return
            except Exception:
                pass
        extra = {
            "component": "plugins",
            "outcome": outcome,
            "action": action,
            **payload,
        }
        _LOGGER.log(level, action, extra=extra)
    except Exception:
        return


def log_plugin_loaded(backend):
    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend=backend)


def log_permission_denied(request, error_code):
    """Record a denial for an authenticated user with ids only. Never changes the exception that follows."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return
    event = getattr(request, "event", None)
    req_id = getattr(request, "request_id", None) or current_request_id()
    log_operation(
        "permission.denied",
        OUTCOME_FAILURE,
        backend="teamshifts",
        error_code=error_code,
        event_id=getattr(event, "pk", None),
        user_id=getattr(user, "pk", None),
        request_id=req_id,
    )


def log_job(
    action,
    outcome,
    job_name,
    *,
    event_id=None,
    object_id=None,
    error_code=None,
    duration_ms=None,
    job_id=None,
):
    """Emit one privacy-safe Celery job lifecycle line."""
    event_pk = getattr(event_id, "pk", event_id)
    log_operation(
        action,
        outcome,
        backend="teamshifts",
        job_name=job_name,
        event_id=event_pk,
        object_id=object_id,
        error_code=error_code,
        duration_ms=duration_ms,
        job_id=job_id,
    )


def traced_job(job_name):
    """Log job boundaries without changing the return value or exception."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            event_arg = kwargs.get("event") if "event" in kwargs else (args[0] if args else None)
            event_id = getattr(event_arg, "pk", event_arg)
            if isinstance(event_id, bool) or not isinstance(event_id, int):
                event_id = None
            started = time.monotonic()
            with job_correlation():
                log_job("job.start", OUTCOME_SUCCESS, job_name, event_id=event_id)
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    retried = type(exc).__name__ == "Retry"
                    log_job(
                        "job.retry" if retried else "job.fail",
                        OUTCOME_FAILURE,
                        job_name,
                        event_id=event_id,
                        error_code="retry" if retried else type(exc).__name__,
                        duration_ms=elapsed_ms(started),
                    )
                    raise
                log_job(
                    "job.finish",
                    OUTCOME_SUCCESS,
                    job_name,
                    event_id=event_id,
                    duration_ms=elapsed_ms(started),
                )
                return result

        return wrapper

    return decorator

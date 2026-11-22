"""Privacy-safe operational logs for this plugin.

Delegates to the host logger when it is importable. Otherwise uses
``eventyay.plugins``. Logging never raises into product code.

Emails, names, tokens, secrets, and request or response bodies are dropped.
Only opaque IDs, status codes, durations, and safe error codes are kept.
"""

from __future__ import annotations

import functools
import logging
import re

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LOGGER = logging.getLogger("eventyay.plugins")
_INT_FIELDS = frozenset(
    {
        "event_id",
        "user_id",
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
    }
)

try:
    from eventyay.base.operational_logging import log_event as _HOST_LOG_EVENT
except Exception:
    _HOST_LOG_EVENT = None


def _safe_identifier(value):
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return None


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
    log_operation(
        "permission.denied",
        OUTCOME_FAILURE,
        backend="teamshifts",
        error_code=error_code,
        event_id=getattr(event, "pk", None),
        user_id=getattr(user, "pk", None),
    )


def log_job(action, outcome, job_name, *, event_id=None, object_id=None, error_code=None):
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
                )
                raise
            log_job("job.finish", OUTCOME_SUCCESS, job_name, event_id=event_id)
            return result

        return wrapper

    return decorator

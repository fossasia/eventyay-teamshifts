from eventyay.base.email import get_available_placeholders

from ..models import EmailTemplateRoles
from . import MAIL_CONTEXT_KEY

APPLICANT_CONTEXT = ["event", MAIL_CONTEXT_KEY, "user"]
SHIFT_CONTEXT = [*APPLICANT_CONTEXT, "shift", "role"]
COMPOSE_CONTEXT = APPLICANT_CONTEXT

ROLE_CONTEXTS = {
    EmailTemplateRoles.APPLICATION_RECEIVED: APPLICANT_CONTEXT,
    EmailTemplateRoles.APPLICATION_ACCEPTED: APPLICANT_CONTEXT,
    EmailTemplateRoles.APPLICATION_REJECTED: APPLICANT_CONTEXT,
    EmailTemplateRoles.MEMBER_ADDED_BY_ORGANIZER: APPLICANT_CONTEXT,
    EmailTemplateRoles.CERTIFICATE_GENERATED: APPLICANT_CONTEXT,
    EmailTemplateRoles.NEW_APPLICATION_ORGANIZER: [*APPLICANT_CONTEXT, "application"],
    EmailTemplateRoles.SHIFT_ASSIGNED_BY_ORGANIZER: SHIFT_CONTEXT,
    EmailTemplateRoles.SHIFT_CLAIMED_BY_VOLUNTEER: SHIFT_CONTEXT,
    EmailTemplateRoles.SHIFT_DROPPED_BY_VOLUNTEER: SHIFT_CONTEXT,
    EmailTemplateRoles.SHIFT_DROPPED_ORGANIZER: [*SHIFT_CONTEXT, "application"],
    EmailTemplateRoles.VOUCHER_SENT: [*APPLICANT_CONTEXT, "voucher"],
}

PREVIEW_CONTEXT = sorted({key for context in [COMPOSE_CONTEXT, *ROLE_CONTEXTS.values()] for key in context})


def mail_context(role):
    """What an email of this template role is rendered with; composed emails for anything else."""
    return ROLE_CONTEXTS.get(role, COMPOSE_CONTEXT)


def available_placeholders(event, context):
    """Placeholders that resolve in emails rendered with ``context``, in name order."""
    placeholders = get_available_placeholders(event, list(context))
    return [placeholders[name] for name in sorted(placeholders) if placeholders[name].is_visible]


def placeholder_names(event, context):
    return [placeholder.identifier for placeholder in available_placeholders(event, context)]


def placeholder_help(event, context):
    return [(f"{{{placeholder.identifier}}}", placeholder.explanation) for placeholder in available_placeholders(event, context)]

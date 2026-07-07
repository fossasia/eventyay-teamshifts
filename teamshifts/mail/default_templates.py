from django.utils.translation import gettext_noop as _
from i18nfield.strings import LazyI18nString

RECEIVED_SUBJECT = LazyI18nString.from_gettext(_("We received your application"))
RECEIVED_TEXT = LazyI18nString.from_gettext(
    _(
        "Hi {full_name},\n\n"
        "Thanks for applying to join the team for {event_name}. "
        "We have received your application for the role of {role_name} "
        "and will get back to you soon.\n\n"
        "Best regards,\n"
        "The {event_name} team"
    )
)

ACCEPTED_SUBJECT = LazyI18nString.from_gettext(_("Your application was accepted"))
ACCEPTED_TEXT = LazyI18nString.from_gettext(
    _(
        "Hi {full_name},\n\n"
        "Great news — your application for the role of {role_name} at "
        "{event_name} has been accepted. Welcome to the team.\n\n"
        "You will receive further details about your shift shortly.\n\n"
        "Best regards,\n"
        "The {event_name} team"
    )
)

REJECTED_SUBJECT = LazyI18nString.from_gettext(_("Update on your application"))
REJECTED_TEXT = LazyI18nString.from_gettext(
    _(
        "Hi {full_name},\n\n"
        "Thank you for your interest in joining the {event_name} team as "
        "a {role_name}. Unfortunately, we are unable to accept your "
        "application at this time.\n\n"
        "We appreciate your enthusiasm and hope you enjoy the event.\n\n"
        "Best regards,\n"
        "The {event_name} team"
    )
)


def get_default_template(role: str) -> tuple[LazyI18nString, LazyI18nString]:
    from ..models import EmailTemplateRoles

    mapping = {
        EmailTemplateRoles.APPLICATION_RECEIVED: (RECEIVED_SUBJECT, RECEIVED_TEXT),
        EmailTemplateRoles.APPLICATION_ACCEPTED: (ACCEPTED_SUBJECT, ACCEPTED_TEXT),
        EmailTemplateRoles.APPLICATION_REJECTED: (REJECTED_SUBJECT, REJECTED_TEXT),
    }
    return mapping[role]

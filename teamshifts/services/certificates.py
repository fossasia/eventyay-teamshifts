import base64
import json
import logging
import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.formats import date_format
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext
from django_scopes import scope
from eventyay.base.email import get_email_context
from eventyay.base.i18n import language
from eventyay.base.services.mail import mail_send_task
from i18nfield.strings import LazyI18nString

from ..models import (
    ApplicationStatus,
    CertificateMatchMode,
    CertificateSettings,
    CertificateTrigger,
    EmailTemplateRoles,
    MemberCertificate,
    ShiftAssignment,
    TeamMemberApplication,
)
from ..pdf import default_layout, format_event_date_from, format_event_date_to, format_event_dates, layout_is_initial_overlay, render_certificate_pdf

logger = logging.getLogger(__name__)


def get_certificate_settings(event) -> CertificateSettings:
    with scope(event=event):
        settings, created = CertificateSettings.objects.get_or_create(event=event)
        if created or not settings.layout or layout_is_initial_overlay(settings.layout):
            settings.layout = json.dumps(default_layout())
            settings.save(update_fields=["layout"])
    return settings


def qualifying_assignments(application: TeamMemberApplication):
    with scope(event=application.event):
        return list(
            ShiftAssignment.objects.filter(
                team_member=application.user,
                shift__event=application.event,
            )
            .select_related("role", "shift")
            .order_by("shift__start_time")
        )


def completed_shift_count(application: TeamMemberApplication) -> int:
    with scope(event=application.event):
        return ShiftAssignment.objects.filter(
            team_member=application.user,
            shift__event=application.event,
            ended_at__isnull=False,
        ).count()


def application_context(application: TeamMemberApplication) -> dict:
    assignments = list(qualifying_assignments(application))
    roles = sorted({assignment.role.name for assignment in assignments if assignment.role_id})
    user = application.user
    event = application.event

    member_name = (user.fullname or "").strip() or user.email
    event_name = str(event.name)
    location = str(event.location) if event.location else ""
    date_from = format_event_date_from(event)
    date_to = format_event_date_to(event)
    issued = now()

    body_line2 = gettext("the %(event_name)s, held from %(date_from)s to %(date_to)s, in %(location)s.") % {
        "event_name": event_name,
        "date_from": date_from,
        "date_to": date_to,
        "location": location,
    }

    return {
        "certificate_title": gettext("Certificate of Appreciation"),
        "certificate_intro": gettext("presents this"),
        "certificate_body_line1": gettext("For your dedication and outstanding contributions at"),
        "certificate_body_line2": body_line2,
        "member_name": member_name,
        "member_email": user.email or "",
        "event_name": event_name,
        "event_dates": format_event_dates(event),
        "event_date_from": date_from,
        "event_date_to": date_to,
        "event_location": location,
        "organizer_name": str(event.organizer.name),
        "completed_shift_count": str(sum(1 for a in assignments if a.ended_at)),
        "assigned_shift_count": str(len(assignments)),
        "roles": ", ".join(roles),
        "issued_date": gettext("Date Issued: %(date)s") % {"date": date_format(issued, "DATE_FORMAT")},
    }


def member_qualifies(
    application: TeamMemberApplication,
    settings: CertificateSettings | None = None,
    completed_count: int | None = None,
) -> bool:
    if application.status != ApplicationStatus.ACCEPTED:
        return False
    settings = settings or get_certificate_settings(application.event)
    checks = []
    if settings.require_arrived:
        checks.append(bool(application.arrived))
    if settings.require_min_shifts:
        count = completed_count if completed_count is not None else completed_shift_count(application)
        checks.append(count >= settings.min_shifts)
    if not checks:
        return False
    if settings.match_mode == CertificateMatchMode.ANY:
        return any(checks)
    return all(checks)


def qualifying_applications(event, settings: CertificateSettings | None = None):
    settings = settings or get_certificate_settings(event)
    with scope(event=event):
        applications = list(TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).select_related("user"))
    return [application for application in applications if member_qualifies(application, settings)]


def certificate_filename(application: TeamMemberApplication) -> str:
    name = slugify((application.user.fullname or "").strip() or application.user.email.split("@")[0]) or "member"
    return f"{application.event.slug}-{name}.pdf"


def generate_certificate(application: TeamMemberApplication, settings: CertificateSettings | None = None) -> MemberCertificate:
    settings = settings or get_certificate_settings(application.event)
    pdf_bytes = render_certificate_pdf(settings, application_context(application))
    is_first_generation = False
    with scope(event=application.event):
        certificate, _created = MemberCertificate.objects.get_or_create(application=application)
        if certificate.notified_at is None:
            is_first_generation = True
        if certificate.file:
            certificate.file.delete(save=False)
        certificate.file.save(certificate_filename(application), ContentFile(pdf_bytes), save=False)
        certificate.generated_at = now()
        certificate.downloaded_at = None
        certificate.save()

    if is_first_generation:
        transaction.on_commit(lambda: _send_certificate_email(application, pdf_bytes))

    return certificate


def _send_certificate_email(application: TeamMemberApplication, pdf_bytes: bytes) -> None:
    user = application.user
    event = application.event

    if not user or not user.email:
        logger.warning("[TeamShifts] Skipping certificate email for application %s: no email address", application.pk)
        return

    try:
        cfm = event.call_for_team_members
    except Exception:
        logger.warning("[TeamShifts] No CFM for event %s — skipping certificate email", event.slug)
        return

    template = cfm.get_mail_template(EmailTemplateRoles.CERTIFICATE_GENERATED)
    locale = user.locale or event.settings.locale

    with language(locale, event.settings.region):
        context = get_email_context(event=event, user=user)
        subject = str(LazyI18nString(template.subject).localize(locale))
        body_template = LazyI18nString(template.body).localize(locale)
        body = str(body_template).format_map(context)

        sender = event.settings.mail_from
        event_backend = event.get_mail_backend()
        sender = event_backend.from_address if hasattr(event_backend, "from_address") else sender

    try:
        mail_send_task.apply_async(
            kwargs={
                "to": [user.email],
                "subject": subject,
                "body": body,
                "html": None,
                "sender": sender or event.settings.mail_from,
                "event": event.pk,
                "user": user.pk,
                "attach_file_base64": base64.b64encode(pdf_bytes).decode(),
                "attach_file_name": certificate_filename(application),
            }
        )
        with scope(event=event):
            MemberCertificate.objects.filter(application=application).update(notified_at=now())
        logger.info("[TeamShifts] Certificate email queued for application %s", application.pk)
    except Exception:
        logger.exception("[TeamShifts] Failed to queue certificate email for application %s", application.pk)


def maybe_auto_issue_certificate(application: TeamMemberApplication) -> MemberCertificate | None:
    settings = get_certificate_settings(application.event)
    if settings.trigger != CertificateTrigger.AUTO:
        return None
    if not member_qualifies(application, settings):
        # Member no longer qualifies — revoke any existing certificate
        _revoke_certificate(application)
        return None
    try:
        return generate_certificate(application, settings)
    except (OSError, ValueError):
        logger.exception("Failed to auto-generate member certificate for application %s", application.pk)
        return None


def _revoke_certificate(application: TeamMemberApplication) -> None:
    with scope(event=application.event):
        try:
            cert = MemberCertificate.objects.get(application=application)
        except MemberCertificate.DoesNotExist:
            return
        if cert.file:
            cert.file.delete(save=False)
        cert.delete()


def generate_all_certificates(event) -> int:
    settings = get_certificate_settings(event)
    count = 0
    for application in qualifying_applications(event, settings):
        generate_certificate(application, settings)
        count += 1
    return count


def zip_generated_certificates(event) -> BytesIO | None:
    settings = get_certificate_settings(event)
    qualifying = set(app.pk for app in qualifying_applications(event, settings))
    with scope(event=event):
        certificates = list(
            MemberCertificate.objects.filter(
                application__event=event,
                application__pk__in=qualifying,
                file__isnull=False,
            ).select_related("application", "application__user", "application__event")
        )
    if not certificates:
        return None
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used = set()
        for certificate in certificates:
            filename = certificate_filename(certificate.application)
            if filename in used:
                filename = f"{certificate.pk}-{filename}"
            used.add(filename)
            with certificate.file.open("rb") as handle:
                archive.writestr(filename, handle.read())
    buffer.seek(0)
    return buffer

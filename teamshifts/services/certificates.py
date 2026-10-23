import json
import logging
import zipfile
from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils.formats import date_format
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext
from django_scopes import scope, scopes_disabled
from eventyay.base.email import get_email_context
from eventyay.base.models import CachedFile, Event
from eventyay.base.services.mail import SendMailException, mail
from eventyay.base.services.tasks import ProfiledEventTask
from eventyay.celery_app import app
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
from ..tasks import MarkdownBreakString
from .email import record_sent_email

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
    event_color = getattr(event, "visible_primary_color", None)
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
        "_event_color": event_color,
    }


def event_last_day(event) -> date:
    return event.date_to.astimezone(event.tz).date()


def event_end(event) -> datetime:
    return datetime.combine(event_last_day(event) + timedelta(days=1), time.min, tzinfo=event.tz)


def event_has_ended(event) -> bool:
    return now() >= event_end(event)


def member_qualifies(
    application: TeamMemberApplication,
    settings: CertificateSettings | None = None,
    completed_count: int | None = None,
) -> bool:
    if application.status != ApplicationStatus.ACCEPTED:
        return False
    settings = settings or get_certificate_settings(application.event)
    if settings.require_after_event_end and not event_has_ended(application.event):
        return False
    checks = []
    if settings.require_arrived:
        checks.append(bool(application.arrived))
    if settings.require_min_shifts:
        count = completed_count if completed_count is not None else completed_shift_count(application)
        checks.append(count >= settings.min_shifts)
    if not checks:
        return settings.require_after_event_end
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
    with scope(event=application.event):
        certificate, _created = MemberCertificate.objects.get_or_create(application=application)
        old_file_name = certificate.file.name if certificate.file else None
        certificate.file.save(certificate_filename(application), ContentFile(pdf_bytes), save=False)
        certificate.generated_at = now()
        certificate.save()
        if old_file_name:
            certificate.file.storage.delete(old_file_name)
    return certificate


def send_certificate(certificate: MemberCertificate, resend: bool = False) -> bool:
    application = certificate.application
    with scope(event=application.event):
        unsent = MemberCertificate.objects.filter(pk=certificate.pk).exclude(file="").exclude(file__isnull=True)
        if not resend:
            unsent = unsent.filter(notified_at__isnull=True)
        claimed = unsent.update(notified_at=now())
    if claimed:
        transaction.on_commit(lambda: _send_certificate_email(application, certificate))
    return bool(claimed)


def issue_certificate(application: TeamMemberApplication, settings: CertificateSettings | None = None) -> MemberCertificate:
    with scope(event=application.event):
        certificate = MemberCertificate.objects.filter(application=application).first()
    if certificate and certificate.notified_at:
        return certificate
    if not certificate or not certificate.file:
        certificate = generate_certificate(application, settings)
    send_certificate(certificate)
    return certificate


def _send_certificate_email(application: TeamMemberApplication, certificate: MemberCertificate) -> None:
    user = application.user
    event = application.event

    if not user or not user.email:
        logger.warning("[TeamShifts] Skipping certificate email for application %s: no email address", application.pk)
        return

    try:
        cfm = event.call_for_team_members
    except ObjectDoesNotExist:
        logger.warning("[TeamShifts] No CFM for event %s — skipping certificate email", event.slug)
        return

    template = cfm.get_mail_template(EmailTemplateRoles.CERTIFICATE_GENERATED)
    locale = user.locale or event.settings.locale

    context = get_email_context(event=event, user=user)
    subject = LazyI18nString(template.subject)
    body = MarkdownBreakString(LazyI18nString(template.body))

    try:
        cf = CachedFile.objects.create(
            filename=certificate_filename(application),
            type="application/pdf",
            expires=now() + timedelta(hours=24),
        )
        with certificate.file.open("rb") as handle:
            cf.file.save(certificate_filename(application), ContentFile(handle.read()), save=True)
        mail(
            email=user.email,
            subject=subject,
            template=body,
            context=context,
            event=event,
            locale=locale,
            user=user,
            attach_cached_files=[cf.pk],
            auto_email=False,
        )
        record_sent_email(event, user, template.subject, template.body, context, locale, EmailTemplateRoles.CERTIFICATE_GENERATED)
        logger.info("[TeamShifts] Certificate email queued for application %s", application.pk)
    except (OSError, SendMailException):
        with scope(event=event):
            MemberCertificate.objects.filter(application=application).update(notified_at=None)
        logger.exception("[TeamShifts] Failed to queue certificate email for application %s", application.pk)


def maybe_auto_issue_certificate(application: TeamMemberApplication) -> MemberCertificate | None:
    settings = get_certificate_settings(application.event)
    if settings.trigger != CertificateTrigger.AUTO:
        return None
    if settings.require_after_event_end and not event_has_ended(application.event):
        return None
    if not member_qualifies(application, settings):
        # Member no longer qualifies — revoke any existing certificate
        _revoke_certificate(application)
        return None
    try:
        return issue_certificate(application, settings)
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


UNSENT_CERTIFICATE = Q(certificate__isnull=True) | Q(certificate__notified_at__isnull=True)
GENERATED_CERTIFICATE = Q(certificate__file__isnull=False) & ~Q(certificate__file="")


def certificates_to_generate(event, settings: CertificateSettings | None = None) -> list[TeamMemberApplication]:
    settings = settings or get_certificate_settings(event)
    with scope(event=event):
        applications = list(TeamMemberApplication.objects.filter(UNSENT_CERTIFICATE, event=event, status=ApplicationStatus.ACCEPTED).select_related("user"))
    return [application for application in applications if member_qualifies(application, settings)]


def certificates_to_send(event, settings: CertificateSettings | None = None) -> list[TeamMemberApplication]:
    settings = settings or get_certificate_settings(event)
    with scope(event=event):
        applications = list(
            TeamMemberApplication.objects.filter(
                GENERATED_CERTIFICATE,
                event=event,
                status=ApplicationStatus.ACCEPTED,
                certificate__notified_at__isnull=True,
            ).select_related("user", "certificate")
        )
    return [application for application in applications if member_qualifies(application, settings)]


def generate_certificates(event) -> dict:
    settings = get_certificate_settings(event)
    result = {"generated": 0, "failed": 0}
    for application in certificates_to_generate(event, settings):
        try:
            generate_certificate(application, settings)
        except (OSError, ValueError):
            logger.exception("Failed to generate member certificate for application %s", application.pk)
            result["failed"] += 1
        else:
            result["generated"] += 1
    return result


def send_generated_certificates(event) -> dict:
    result = {"sent": 0}
    for application in certificates_to_send(event):
        if send_certificate(application.certificate):
            result["sent"] += 1
    return result


def send_certificates(event, applications) -> dict:
    settings = get_certificate_settings(event)
    result = {"sent": 0, "resent": 0, "not_qualified": 0, "no_email": 0, "failed": 0}
    for application in applications:
        if not member_qualifies(application, settings):
            result["not_qualified"] += 1
            continue
        if not application.user.email:
            result["no_email"] += 1
            continue
        with scope(event=event):
            sent_certificate = MemberCertificate.objects.filter(application=application, notified_at__isnull=False).exclude(file="").first()
        if sent_certificate:
            send_certificate(sent_certificate, resend=True)
            result["resent"] += 1
            continue
        try:
            issue_certificate(application, settings)
        except (OSError, ValueError):
            logger.exception("Failed to send member certificate for application %s", application.pk)
            result["failed"] += 1
        else:
            result["sent"] += 1
    return result


@app.task(base=ProfiledEventTask, name="teamshifts.generate_certificates")
def generate_certificates_task(event: Event) -> dict:
    return generate_certificates(event)


@app.task(base=ProfiledEventTask, name="teamshifts.send_generated_certificates")
def send_generated_certificates_task(event: Event) -> dict:
    return send_generated_certificates(event)


@app.task(base=ProfiledEventTask, name="teamshifts.send_selected_certificates")
def send_selected_certificates_task(event: Event, application_ids: list[int]) -> dict:
    with scope(event=event):
        applications = list(TeamMemberApplication.objects.filter(event=event, pk__in=application_ids, status=ApplicationStatus.ACCEPTED).select_related("user"))
    return send_certificates(event, applications)


def issues_at_event_end(settings: CertificateSettings, event) -> bool:
    since = settings.after_event_end_issuing_since
    return settings.issues_after_event_end and since is not None and since < event_end(event)


def generate_pending_certificates(event) -> int:
    settings = get_certificate_settings(event)
    if not issues_at_event_end(settings, event) or not event_has_ended(event):
        return 0
    with scope(event=event):
        pending = list(
            TeamMemberApplication.objects.filter(
                UNSENT_CERTIFICATE,
                event=event,
                status=ApplicationStatus.ACCEPTED,
            ).select_related("user")
        )
    count = 0
    for application in pending:
        if not member_qualifies(application, settings):
            continue
        try:
            issue_certificate(application, settings)
        except (OSError, ValueError):
            logger.exception("Failed to generate member certificate for application %s", application.pk)
        else:
            count += 1
    return count


def events_awaiting_certificates() -> list[int]:
    with scopes_disabled():
        event_ids = (
            TeamMemberApplication.objects.filter(
                UNSENT_CERTIFICATE,
                status=ApplicationStatus.ACCEPTED,
                event__teamshifts_certificate_settings__trigger=CertificateTrigger.AUTO,
                event__teamshifts_certificate_settings__require_after_event_end=True,
                event__teamshifts_certificate_settings__after_event_end_issuing_since__isnull=False,
                event__date_to__lte=now(),
            )
            .values_list("event_id", flat=True)
            .distinct()
        )
        candidates = CertificateSettings.objects.filter(event_id__in=list(event_ids)).select_related("event")
        return [settings.event_id for settings in candidates if issues_at_event_end(settings, settings.event)]


@app.task(base=ProfiledEventTask, name="teamshifts.issue_event_end_certificates")
def issue_event_end_certificates_task(event: Event):
    count = generate_pending_certificates(event)
    if count:
        logger.info("[TeamShifts] Issued %s certificates after the end of event %s", count, event.slug)


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

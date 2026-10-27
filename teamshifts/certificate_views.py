import json
import mimetypes

from django.contrib import messages
from django.core.files.base import ContentFile
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.functional import cached_property
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext
from django.views.generic import View
from django_scopes import scope
from eventyay.base.models import CachedFile
from eventyay.base.views.tasks import AsyncAction
from eventyay.control.views.pdf import BaseEditorView

from .forms import CertificateSettingsForm
from .models import ApplicationStatus, CertificateTrigger, MemberCertificate, TeamMemberApplication
from .pdf import (
    CERTIFICATE_PLACEHOLDERS,
    default_layout,
    editor_image_variables,
    editor_variables,
    get_default_background_url,
    image_file_to_pdf,
    open_default_background,
    preview_context,
    render_certificate_pdf,
)
from .permissions import TeamShiftsPermissionRequiredMixin, can_view_email_addresses
from .services.certificates import (
    event_has_ended,
    event_last_day,
    generate_certificates_task,
    get_certificate_settings,
    member_qualifies,
    send_generated_certificates_task,
    zip_generated_certificates,
)
from .views import PluginActiveMixin

IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg"}


class CertificateSettingsView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"
    template_name = "teamshifts/certificates.html"

    def get(self, request, *args, **kwargs):
        settings = get_certificate_settings(request.event)
        form = CertificateSettingsForm(instance=settings)
        return render(request, self.template_name, self._context(request, form, settings))

    def post(self, request, *args, **kwargs):
        settings = get_certificate_settings(request.event)
        action = request.POST.get("action")
        if action == "download_all":
            archive = zip_generated_certificates(request.event)
            if not archive:
                messages.error(request, _("No generated certificates are available to download yet."))
                return redirect(self._url(request))
            response = FileResponse(archive, content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="certificates-{request.event.slug}.zip"'
            return response

        form = CertificateSettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            messages.success(request, _("Certificate settings have been saved."))
            return redirect(self._url(request))
        messages.error(request, _("We could not save your changes. See below for details."))
        return render(request, self.template_name, self._context(request, form, settings))

    def _url(self, request):
        return reverse(
            "plugins:teamshifts:certificates",
            kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
        )

    def _context(self, request, form, settings):
        event = request.event
        with scope(event=event):
            members = list(
                TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED)
                .select_related("user")
                .annotate(
                    _completed_shift_count=Count(
                        "user__shift_assignments",
                        filter=Q(user__shift_assignments__shift__event=event, user__shift_assignments__ended_at__isnull=False),
                    )
                )
            )
            certs = {certificate.application_id: certificate for certificate in MemberCertificate.objects.filter(application__in=members)}
        rows = []
        for member in members:
            certificate = certs.get(member.pk)
            completed = member._completed_shift_count
            rows.append(
                {
                    "member": member,
                    "qualifies": member_qualifies(member, settings, completed_count=completed),
                    "completed": completed,
                    "generated": bool(certificate and certificate.file),
                    "sent": bool(certificate and certificate.notified_at),
                    "downloaded": bool(certificate and certificate.downloaded_at),
                }
            )
        qualifying = [row for row in rows if row["qualifies"]]
        generate_count = sum(1 for row in qualifying if not row["sent"])
        send_count = sum(1 for row in qualifying if row["generated"] and not row["sent"])
        members_url = reverse("plugins:teamshifts:members", kwargs={"organizer": request.organizer.slug, "event": event.slug})
        return {
            "form": form,
            "settings": settings,
            "members_url": members_url,
            "generate_count": generate_count,
            "send_count": send_count,
            "can_download": any(row["generated"] for row in qualifying),
            "generation_hint": self._generation_hint(event, settings, qualifying, generate_count, send_count, members_url),
            "placeholders": CERTIFICATE_PLACEHOLDERS,
            "rows": rows,
            "can_view_email": can_view_email_addresses(request.user, request.organizer, event, request=request),
        }

    def _generation_hint(self, event, settings, qualifying, generate_count, send_count, members_url):
        if settings.require_after_event_end and not event_has_ended(event):
            last_day = date_format(event_last_day(event), "DATE_FORMAT")
            if settings.trigger == CertificateTrigger.AUTO:
                return _("Certificates will be generated and emailed automatically once the event has ended on %(date)s.") % {"date": last_day}
            return _("Certificates can be generated once the event has ended on %(date)s.") % {"date": last_day}
        if not qualifying:
            return _("No member meets the qualification conditions yet.")
        if send_count:
            return ngettext(
                "%(count)d certificate is ready to send. Generating again updates it, e.g. after a design change.",
                "%(count)d certificates are ready to send. Generating again updates them, e.g. after a design change.",
                send_count,
            ) % {"count": send_count}
        if generate_count:
            return _("Generate certificates first.")
        return format_html(
            ngettext(
                'The certificate has been sent. To resend it, use the <a href="{url}">Members page</a>.',
                'All {count} certificates have been sent. To resend to someone, use the <a href="{url}">Members page</a>.',
                len(qualifying),
            ),
            count=len(qualifying),
            url=members_url,
        )


class CertificateBulkActionView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, AsyncAction, View):
    permission = "can_teamshifts_manage_applicants"

    def get_success_url(self, value):
        return reverse("plugins:teamshifts:certificates", kwargs={"organizer": self.request.organizer.slug, "event": self.request.event.slug})

    def get_error_url(self):
        return self.get_success_url(None)

    def post(self, request, *args, **kwargs):
        return self.do(request.event.pk)


class CertificateGenerateView(CertificateBulkActionView):
    task = generate_certificates_task

    def get_success_message(self, value):
        message = ngettext(
            "Generated %(count)d certificate. No emails were sent.",
            "Generated %(count)d certificates. No emails were sent.",
            value["generated"],
        ) % {"count": value["generated"]}
        if value["failed"]:
            message += " " + ngettext(
                "%(count)d could not be generated. Try again later.",
                "%(count)d could not be generated. Try again later.",
                value["failed"],
            ) % {"count": value["failed"]}
        return message


class CertificateSendView(CertificateBulkActionView):
    task = send_generated_certificates_task

    def get_success_message(self, value):
        return ngettext(
            "Sent %(count)d certificate.",
            "Sent %(count)d certificates.",
            value["sent"],
        ) % {"count": value["sent"]}


class CertificatePreviewView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def get(self, request, *args, **kwargs):
        settings = get_certificate_settings(request.event)
        pdf = render_certificate_pdf(settings, preview_context(request.event))
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="certificate-preview.pdf"'
        response["Cache-Control"] = "no-store"
        return response


class CertificateDefaultPdfView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def get(self, request, *args, **kwargs):
        bg = open_default_background()
        pdf_bytes = bg.read()
        if hasattr(bg, "close"):
            bg.close()
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="certificate-default.pdf"'
        response["Cache-Control"] = "no-store"
        return response


class CertificateEditorView(PluginActiveMixin, BaseEditorView):
    permission = "can_change_settings"
    template_name = "teamshifts/certificate_editor.html"
    accepted_formats = ("application/pdf", "image/png", "image/jpeg")
    title = _("Member certificate layout")

    @cached_property
    def certificate_settings(self):
        return get_certificate_settings(self.request.event)

    def get_variables(self):
        return editor_variables()

    def get_images(self):
        return editor_image_variables()

    def get_layout_settings_key(self):
        return "teamshifts_certificate_layout"

    def get_background_settings_key(self):
        return "teamshifts_certificate_background"

    def get_default_background(self):
        return get_default_background_url()

    def get_current_layout(self):
        layout = self.certificate_settings.layout
        if layout:
            return json.loads(layout)
        return default_layout()

    def get_current_background(self):
        if self.certificate_settings.background:
            return self.certificate_settings.background.url
        return self.get_default_background()

    def save_layout(self, layout_data=None):
        if layout_data is None:
            layout_data = self._get_posted_layout_json()
        self.certificate_settings.layout = layout_data or "[]"
        self.certificate_settings.save(update_fields=["layout"])

    def save_background(self, f: CachedFile):
        if not f.file:
            return
        if f.file.name.endswith("empty.pdf"):
            return
        if self.certificate_settings.background:
            self.certificate_settings.background.delete(save=False)
        self.certificate_settings.background.save("background.pdf", f.file)

    def _open_saved_background_pdf(self):
        if self.certificate_settings.background and self.certificate_settings.background.name:
            return self.certificate_settings.background.open("rb")
        return open_default_background()

    def process_upload(self):
        uploaded = self.request.FILES.get("background")
        if not uploaded:
            return _("No file uploaded."), None
        error = False
        if uploaded.size > self.maxfilesize:
            error = _("The uploaded file is too large.")
        if uploaded.size < self.minfilesize:
            error = _("The uploaded file is too small.")
        content_type = (mimetypes.guess_type(uploaded.name)[0] or "").lower()
        if content_type not in self.accepted_formats and not (uploaded.name or "").lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
            error = _("Please only upload PDF or image files.")
        if error:
            return error, None
        if content_type in IMAGE_TYPES or (uploaded.name or "").lower().endswith((".png", ".jpg", ".jpeg")):
            uploaded = ContentFile(image_file_to_pdf(uploaded).read(), name="background.pdf")
        return None, uploaded

    def generate(self, p, override_layout=None, override_background=None):
        background = None
        if override_background is not None:
            background = override_background.open("rb") if hasattr(override_background, "open") else override_background
        pdf = render_certificate_pdf(
            self.certificate_settings,
            preview_context(self.request.event),
            layout=override_layout,
            background_file=background,
        )
        return "certificate-preview.pdf", "application/pdf", pdf

import html as html_mod
import json
import logging
import re
import secrets
from collections import defaultdict
from datetime import timedelta

import dateutil.parser
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, DurationField, ExpressionWrapper, F, Prefetch, Q, Sum
from django.forms import inlineformset_factory
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.formats import date_format
from django.utils.html import strip_tags
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import now
from django.utils.translation import get_language, get_language_info, gettext_lazy as _, ngettext
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import DeleteView, FormView, ListView, TemplateView, View
from django_scopes import scope, scopes_disabled
from eventyay.base.i18n import LazyI18nString
from eventyay.base.models import User
from eventyay.base.templatetags.rich_text import rich_text
from eventyay.control.views import PaginationMixin

from .forms import (
    BaseShiftRoleFormSet,
    CallForTeamMembersApplicationSettingsForm,
    CallForTeamMembersSettingsForm,
    CustomEmailTemplateForm,
    EmailComposeForm,
    EmailQueueEditForm,
    EmailTemplateForm,
    ShiftForm,
    ShiftLocationForm,
    ShiftRoleAssignmentForm,
    TeamApplicationQuestionForm,
    TeamMemberApplicationForm,
    TeamRoleForm,
    render_answer_for_review,
)
from .models import (
    CFM_BUILTIN_FIELD_KEYS,
    CFM_LOCKED_FIELDS,
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsCustomEmailTemplate,
    TeamShiftsEmailQueue,
    normalize_field_order,
)
from .permissions import TeamShiftsPermissionRequiredMixin, can_act_on_role, can_view_email_addresses, get_allowed_role_ids, has_teamshifts_permission
from .services.certificates import maybe_auto_issue_certificate
from .services.email import get_recipients, queue_email, queue_lifecycle_email
from .services.members import AlreadyMemberError, add_member_from_organizer
from .tasks import send_queued_email

logger = logging.getLogger(__name__)


ShiftRoleFormSet = inlineformset_factory(Shift, ShiftRoleAssignment, form=ShiftRoleAssignmentForm, formset=BaseShiftRoleFormSet, extra=1, can_delete=True)


class PluginActiveMixin:
    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class TeamShiftsDashboard(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = None
    template_name = "teamshifts/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            ctx["role_count"] = TeamRole.objects.filter(event=event).count()
            ctx["pending_count"] = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.PENDING).count()
            ctx["accepted_count"] = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).count()
            ctx["shift_count"] = Shift.objects.filter(event=event).count()
            ctx["recent_applications"] = list(
                TeamMemberApplication.objects.filter(event=event, added_by_organizer=False).select_related("user").order_by("-created_at")[:5]
            )
            ctx["accepted_members"] = list(
                TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).select_related("user").order_by("-updated_at")[:8]
            )
            try:
                ctx["cfm"] = event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                ctx["cfm"] = None
        return ctx


class CFMSettingsView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"
    template_name = "teamshifts/cfm_settings.html"

    def _get_cfm(self):
        with scope(event=self.request.event):
            obj, _created = CallForTeamMembers.objects.get_or_create(event=self.request.event)
        return obj

    def get(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        form = CallForTeamMembersSettingsForm(instance=cfm, locales=request.event.settings.locales, event=request.event)

        description = cfm.description.data if cfm.description else {}
        if not isinstance(description, dict):
            description = dict.fromkeys(self.request.event.settings.locales, description or "")

        description_previews = [(code, rich_text(description.get(code, ""))) for code in request.event.settings.locales]

        return render(request, self.template_name, {"form": form, "cfm": cfm, "description_previews": description_previews})

    def post(self, request, *args, **kwargs):
        cfm = self._get_cfm()

        if request.POST.get("action") == "regenerate_secret":
            with scope(event=request.event):
                cfm.regenerate_secret()
            messages.success(request, _("A new secret link has been generated. The old link no longer works."))
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)

        form = CallForTeamMembersSettingsForm(request.POST, instance=cfm, locales=request.event.settings.locales, event=request.event)
        if form.is_valid():
            with scope(event=request.event):
                form.save()
            messages.success(request, _("Settings saved."))
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)

        description = cfm.description.data if cfm.description else {}
        if not isinstance(description, dict):
            description = dict.fromkeys(request.event.settings.locales, description or "")

        description_previews = [(code, rich_text(description.get(code, ""))) for code in request.event.settings.locales]
        return render(request, self.template_name, {"form": form, "cfm": cfm, "description_previews": description_previews})


class CFMApplicationFormView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"
    template_name = "teamshifts/cfm_application_form.html"

    def _get_cfm(self):
        with scope(event=self.request.event):
            obj, _created = CallForTeamMembers.objects.get_or_create(event=self.request.event)
        return obj

    def _questions(self):
        with scope(event=self.request.event):
            return list(TeamApplicationQuestion.objects.filter(event=self.request.event).order_by("pk"))

    def _unified_rows(self, cfm, questions):
        question_map = {q.pk: q for q in questions}
        order = normalize_field_order(list(cfm.field_order))

        present_pks = {int(item) for item in order if not isinstance(item, str)}
        for q in questions:
            if q.pk not in present_pks:
                order.append(q.pk)

        # (Legacy 'role' removal from order has been moved to a database migration)

        label_map = {
            "full_name": _("Full name"),
            "email": _("Email address"),
            "phone": _("Phone / Mobile"),
            "availability": _("Availability notes"),
        }

        rows = []
        for item in order:
            if isinstance(item, str):
                rows.append(
                    {
                        "kind": "builtin",
                        "key": item,
                        "label": label_map.get(item, item),
                        "ask_state": cfm.get_ask_state(item),
                        "locked": item in CFM_LOCKED_FIELDS,
                        "question": None,
                    }
                )
            else:
                q = question_map.get(int(item))
                if q is None:
                    continue
                rows.append(
                    {
                        "kind": "custom",
                        "key": q.pk,
                        "label": str(q.question),
                        "ask_state": None,
                        "locked": False,
                        "question": q,
                    }
                )
        return rows

    def _ctx(self, cfm, form, questions):
        return {
            "form": form,
            "cfm": cfm,
            "questions": questions,
            "unified_rows": self._unified_rows(cfm, questions),
        }

    def get(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        questions = self._questions()
        form = CallForTeamMembersApplicationSettingsForm(instance=cfm)
        return render(request, self.template_name, self._ctx(cfm, form, questions))

    def post(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        questions = self._questions()
        form = CallForTeamMembersApplicationSettingsForm(request.POST, instance=cfm)
        if form.is_valid():
            with scope(event=request.event):
                form.save()
            messages.success(request, _("Settings saved."))
            return redirect("plugins:teamshifts:cfm_application_form", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, self._ctx(cfm, form, questions))


class CFMDescriptionPreviewView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    """Render draft description text with the same Markdown conversion as the public call page."""

    permission = "can_teamshifts_manage_applicants"

    def post(self, request, *args, **kwargs):
        event_locales = list(request.event.settings.locales)
        widget = CallForTeamMembersSettingsForm(locales=event_locales).fields["description"].widget
        raw_values = widget.value_from_datadict(request.POST, request.FILES, "description")
        if not isinstance(raw_values, (list, tuple)):
            raw_values = [raw_values]

        msgs = {}
        for i, code in enumerate(event_locales):
            if i < len(raw_values):
                text = raw_values[i]
                msgs[code] = str(rich_text(text)) if text else ""

        return JsonResponse({"msgs": msgs})


class RichTextPreviewView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = None

    def post(self, request, *args, **kwargs):
        html = request.POST.get("content", "")
        return JsonResponse({"html": str(rich_text(html)) if html else ""})


class TeamRoleListView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = None
    template_name = "teamshifts/roles.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            roles = list(TeamRole.objects.filter(event=request.event))
        allowed = get_allowed_role_ids(request.user, request.organizer, request.event, request=request)
        can_create = has_teamshifts_permission(request.user, request.organizer, request.event, "can_teamshifts_create_roles", request=request)
        return render(
            request,
            self.template_name,
            {"roles": roles, "form": TeamRoleForm() if can_create else None, "allowed_role_ids": allowed, "can_create_roles": can_create},
        )

    def post(self, request, *args, **kwargs):
        if not has_teamshifts_permission(request.user, request.organizer, request.event, "can_teamshifts_create_roles", request=request):
            raise PermissionDenied(_("You do not have permission to create roles."))
        form = TeamRoleForm(request.POST)
        if form.is_valid():
            role = form.save(commit=False)
            role.event = request.event
            with scope(event=request.event):
                role.save()
            messages.success(request, _("Role '%s' created.") % role.name)
            return redirect("plugins:teamshifts:roles", organizer=request.organizer.slug, event=request.event.slug)
        with scope(event=request.event):
            roles = list(TeamRole.objects.filter(event=request.event))
        allowed = get_allowed_role_ids(request.user, request.organizer, request.event, request=request)
        return render(request, self.template_name, {"roles": roles, "form": form, "allowed_role_ids": allowed, "can_create_roles": True})


class TeamRoleDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = None

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            role = get_object_or_404(TeamRole, pk=kwargs["pk"], event=event)
            if not can_act_on_role(request.user, request.organizer, event, role.pk, request=request):
                raise PermissionDenied(_("You do not have permission to manage this role."))
            if role.shift_assignments.exists():
                messages.error(request, _("Cannot delete '%s': it is used by existing shifts.") % role.name)
            else:
                name = role.name
                role.delete()
                messages.success(request, _("Role '%s' deleted.") % name)
        return redirect("plugins:teamshifts:roles", organizer=request.organizer.slug, event=event.slug)


class TeamRoleEditView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = None
    template_name = "teamshifts/role_edit.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            role = get_object_or_404(TeamRole, pk=kwargs["pk"], event=request.event)
            if not can_act_on_role(request.user, request.organizer, request.event, role.pk, request=request):
                raise PermissionDenied(_("You do not have permission to manage this role."))
            form = TeamRoleForm(instance=role)
        return render(request, self.template_name, {"form": form, "role": role})

    def post(self, request, *args, **kwargs):
        with scope(event=request.event):
            role = get_object_or_404(TeamRole, pk=kwargs["pk"], event=request.event)
            if not can_act_on_role(request.user, request.organizer, request.event, role.pk, request=request):
                raise PermissionDenied(_("You do not have permission to manage this role."))
            form = TeamRoleForm(request.POST, instance=role)
            if form.is_valid():
                form.save()
                messages.success(request, _("Role '%s' updated.") % role.name)
                return redirect("plugins:teamshifts:roles", organizer=request.organizer.slug, event=request.event.slug)
            role.refresh_from_db()
        return render(request, self.template_name, {"form": form, "role": role})


class EmailTemplateListView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/email_templates.html"

    def _get_panels(self, request, post_data=None):
        event = request.event
        locales = event.settings.locales
        with scope(event=event):
            try:
                cfm = event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                raise Http404 from None

        from .mail.default_templates import get_default_template

        panels = []
        for role in EmailTemplateRoles.values:
            with scope(event=event):
                template = cfm.get_mail_template(role)
            form = EmailTemplateForm(
                post_data,
                instance=template,
                prefix=role,
                locales=locales,
            )
            is_customised = False
            if template.pk:
                default_subject, default_body = get_default_template(role)
                db_subject = str(template.subject).replace("\r\n", "\n").strip()
                db_body = str(template.body).replace("\r\n", "\n").strip()
                def_subject = str(default_subject).replace("\r\n", "\n").strip()
                def_body = str(default_body).replace("\r\n", "\n").strip()
                if db_subject != def_subject or db_body != def_body:
                    is_customised = True
            panels.append(
                {
                    "role": role,
                    "role_slug": role.replace(".", "_"),
                    "label": EmailTemplateRoles(role).label,
                    "form": form,
                    "is_customised": is_customised,
                }
            )
        return panels

    def _get_custom_panels(self, request, post_data=None):
        locales = request.event.settings.locales
        with scope(event=request.event):
            templates = list(TeamShiftsCustomEmailTemplate.objects.filter(event=request.event))
        panels = []
        for template in templates:
            form = CustomEmailTemplateForm(
                post_data,
                instance=template,
                prefix=f"custom_{template.pk}",
                locales=locales,
            )
            panels.append(
                {
                    "pk": template.pk,
                    "label": template.name,
                    "role_slug": f"custom_{template.pk}",
                    "form": form,
                    "is_custom": True,
                }
            )
        return panels

    def get(self, request, *args, **kwargs):
        panels = self._get_panels(request)
        custom_panels = self._get_custom_panels(request)
        return render(
            request,
            self.template_name,
            {
                "panels": panels,
                "custom_panels": custom_panels,
                "locales": request.event.settings.locales,
                "email_placeholders": [
                    ("{full_name}", _("The applicant's full name")),
                    ("{event_name}", _("The event's name")),
                    ("{role_name}", _("The role applied for")),
                    ("{event_dates}", _("The event's date range")),
                    ("{event_location}", _("The event's location")),
                    ("{shift_schedule_url}", _("Link to the shift schedule")),
                ],
            },
        )

    def post(self, request, *args, **kwargs):
        panels = self._get_panels(request, post_data=request.POST)
        custom_panels = self._get_custom_panels(request, post_data=request.POST)

        builtin_valid = all(p["form"].is_valid() for p in panels)
        custom_valid = all(p["form"].is_valid() for p in custom_panels)

        if builtin_valid and custom_valid:
            with scope(event=request.event):
                for panel in panels:
                    form = panel["form"]
                    template = form.save(commit=False)
                    template.event = request.event
                    template.role = panel["role"]
                    template.save()
                for panel in custom_panels:
                    panel["form"].save()
            messages.success(request, _("Email templates have been saved."))
            return redirect(
                "plugins:teamshifts:email_templates",
                organizer=request.organizer.slug,
                event=request.event.slug,
            )
        return render(
            request,
            self.template_name,
            {
                "panels": panels,
                "custom_panels": custom_panels,
                "locales": request.event.settings.locales,
                "email_placeholders": [
                    ("{full_name}", _("The applicant's full name")),
                    ("{event_name}", _("The event's name")),
                    ("{role_name}", _("The role applied for")),
                    ("{event_dates}", _("The event's date range")),
                    ("{event_location}", _("The event's location")),
                    ("{shift_schedule_url}", _("Link to the shift schedule")),
                ],
            },
        )


class EmailTemplatePreviewView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"

    def post(self, request, *args, **kwargs):
        from collections import defaultdict

        from eventyay.base.i18n import language
        from eventyay.base.templatetags.rich_text import compile_email_body

        event = request.event
        event_locales = list(event.settings.locales)
        from django.utils.html import escape

        region = event.settings.region

        sample_values = defaultdict(
            str,
            {
                "full_name": "Jane Doe",
                "event_name": str(event.name),
                "role_name": "Volunteer",
                "event_dates": event.get_date_range_display(),
                "event_location": str(event.location) if event.location else "",
                "shift_schedule_url": "https://example.com/my-event/shifts/",
            },
        )

        def render_with_placeholders(text):
            highlighted = re.sub(
                r"\{(\w+)\}",
                lambda m: f'<span class="placeholder">{escape(sample_values.get(m.group(1), m.group(0)))}</span>',
                text,
            )
            return compile_email_body(highlighted)

        body_values = request.POST.getlist("body")
        previews = {}
        for i, locale in enumerate(event_locales):
            text = request.POST.get(f"body_{locale}") or (body_values[i] if i < len(body_values) else "")
            with language(locale, region):
                previews[locale] = render_with_placeholders(text)

        return JsonResponse({"previews": previews})


class EmailTemplateEditView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/email_template_edit.html"

    def _get_or_seed(self, request, role):
        if role not in EmailTemplateRoles.values:
            raise Http404
        with scope(event=request.event):
            try:
                cfm = request.event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                raise Http404 from None
            template = cfm.get_mail_template(role)
        return template

    def get(self, request, *args, **kwargs):
        template = self._get_or_seed(request, kwargs["role"])
        form = EmailTemplateForm(instance=template, locales=request.event.settings.locales)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "template": template,
                "role_label": EmailTemplateRoles(template.role).label,
            },
        )

    def post(self, request, *args, **kwargs):
        template = self._get_or_seed(request, kwargs["role"])
        form = EmailTemplateForm(request.POST, instance=template, locales=request.event.settings.locales)
        if form.is_valid():
            with scope(event=request.event):
                form.save()
            messages.success(request, _("Template saved."))
            return redirect(
                "plugins:teamshifts:email_templates",
                organizer=request.organizer.slug,
                event=request.event.slug,
            )
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "template": template,
                "role_label": EmailTemplateRoles(template.role).label,
            },
        )


class QuestionEditView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"
    template_name = "teamshifts/question_edit.html"

    def _get_instance(self, request, pk):
        if pk is None:
            return None
        with scope(event=request.event):
            return get_object_or_404(TeamApplicationQuestion, pk=pk, event=request.event)

    def get(self, request, *args, **kwargs):
        instance = self._get_instance(request, kwargs.get("pk"))
        form = TeamApplicationQuestionForm(instance=instance, event=request.event, locales=request.event.settings.locales)
        return render(request, self.template_name, {"form": form, "question": instance})

    def post(self, request, *args, **kwargs):
        instance = self._get_instance(request, kwargs.get("pk"))
        form = TeamApplicationQuestionForm(request.POST, instance=instance, event=request.event, locales=request.event.settings.locales)
        if form.is_valid():
            with scope(event=request.event):
                saved = form.save()
            if instance is None:
                with scope(event=request.event):
                    try:
                        cfm = request.event.call_for_team_members
                        order = normalize_field_order(list(cfm.field_order))
                        if saved.pk not in order:
                            order.append(saved.pk)
                        cfm.field_order = order
                        cfm.save(update_fields=["field_order"])
                    except CallForTeamMembers.DoesNotExist:
                        pass
                messages.success(request, _("Question '%s' added.") % saved.question)
            else:
                messages.success(request, _("Question saved."))
            return redirect("plugins:teamshifts:cfm_application_form", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form, "question": instance})


class QuestionDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            question = get_object_or_404(TeamApplicationQuestion, pk=kwargs["pk"], event=event)
            label = str(question.question)
            pk = question.pk
            question.delete()
            try:
                cfm = event.call_for_team_members
                cfm.field_order = [item for item in cfm.field_order if item != pk]
                cfm.save(update_fields=["field_order"])
            except CallForTeamMembers.DoesNotExist:
                pass
        messages.success(request, _("Question '%s' deleted.") % label)
        return redirect("plugins:teamshifts:cfm_application_form", organizer=request.organizer.slug, event=event.slug)


class QuestionReorderView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body.decode("utf-8"))
            raw_ids = data.get("ids", [])
        except (json.JSONDecodeError, ValueError, AttributeError):
            return HttpResponse(status=400)

        normalised = []
        for item in raw_ids:
            if isinstance(item, str) and item in CFM_BUILTIN_FIELD_KEYS:
                normalised.append(item)
            elif isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                normalised.append(int(item))
            else:
                return HttpResponse(status=400)

        if len(set(str(i) for i in normalised)) != len(normalised):
            return HttpResponse(status=400)

        event = request.event
        with scope(event=event):
            try:
                cfm = event.call_for_team_members
                cfm.field_order = normalised
                cfm.save(update_fields=["field_order"])
            except CallForTeamMembers.DoesNotExist:
                pass

        return HttpResponse(status=204)


class QuestionToggleView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            question = get_object_or_404(TeamApplicationQuestion, pk=kwargs["pk"], event=event)
            try:
                data = json.loads(request.body.decode())
            except (json.JSONDecodeError, ValueError):
                return JsonResponse({"error": "Invalid JSON"}, status=400)
            field = data.get("field")
            value = data.get("value")
            if field is None or value is None:
                return JsonResponse({"error": "Missing field or value"}, status=400)
            if field == "active":
                if not isinstance(value, bool):
                    return JsonResponse({"error": "Value must be boolean"}, status=400)
                question.active = value
                question.save(update_fields=["active"])
            elif field == "question_required":
                if value not in ("optional", "required"):
                    return JsonResponse({"error": "Invalid value"}, status=400)
                question.required = value == "required"
                question.save(update_fields=["required"])
            else:
                return JsonResponse({"error": f"Invalid field: {field}"}, status=400)
        return JsonResponse({"success": True, "field": field, "value": value})


class ApplicationListView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, PaginationMixin, ListView):
    permission = None
    template_name = "teamshifts/applications.html"
    context_object_name = "applications"

    def get_queryset(self):
        event = self.request.event
        with scope(event=event):
            qs = (
                TeamMemberApplication.objects.filter(event=event, added_by_organizer=False)
                .select_related("user")
                .prefetch_related("answers__question")
                .order_by("-created_at")
            )
            status_filter = self.request.GET.get("status")

            search = self.request.GET.get("q", "").strip()

            if status_filter in ApplicationStatus.values:
                qs = qs.filter(status=status_filter)

            if search:
                can_view_email = can_view_email_addresses(self.request.user, self.request.organizer, event, request=self.request)
                if can_view_email:
                    qs = qs.filter(Q(user__email__icontains=search) | Q(user__fullname__icontains=search))
                else:
                    qs = qs.filter(Q(user__fullname__icontains=search))
            return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            status_filter = self.request.GET.get("status")
            search = self.request.GET.get("q", "").strip()

            try:
                cfm = event.call_for_team_members
                field_order = normalize_field_order(list(cfm.field_order))
            except CallForTeamMembers.DoesNotExist:
                cfm = None
                field_order = list(CFM_BUILTIN_FIELD_KEYS)

            can_view_email = can_view_email_addresses(self.request.user, self.request.organizer, event, request=self.request)

            custom_questions = {str(q.pk): q.question for q in TeamApplicationQuestion.objects.filter(event=event, active=True)}

            active_keys = []
            for k in field_order:
                if k == "role":
                    continue
                if k == "email" and not can_view_email:
                    continue
                if k in CFM_BUILTIN_FIELD_KEYS:
                    if cfm and getattr(cfm, f"ask_{k}", "optional") == "do_not_ask":
                        continue
                    active_keys.append(k)
                elif str(k) in custom_questions:
                    active_keys.append(k)

            dynamic_keys = active_keys[:4]
            columns = []

            for raw_key in dynamic_keys:
                key = str(raw_key)
                if key == "full_name":
                    columns.append({"key": key, "label": _("Name")})
                elif key == "email":
                    columns.append({"key": key, "label": _("Email")})
                elif key == "phone":
                    columns.append({"key": key, "label": _("Phone")})
                elif key == "availability":
                    columns.append({"key": key, "label": _("Availability")})
                elif key in custom_questions:
                    columns.append({"key": key, "label": custom_questions[key]})

            applications = list(ctx["applications"])
            for app in applications:
                app_dynamic_values = []
                answers_dict = {str(a.question_id): render_answer_for_review(a.question, a.answer) for a in app.answers.all()}

                for col in columns:
                    key = col["key"]
                    if key == "full_name":
                        app_dynamic_values.append(app.user.fullname)
                    elif key == "email":
                        app_dynamic_values.append(app.user.email)
                    elif key == "phone":
                        app_dynamic_values.append(app.phone)
                    elif key == "availability":
                        app_dynamic_values.append(app.availability_notes)
                    else:
                        app_dynamic_values.append(answers_dict.get(key, ""))
                app.dynamic_values = app_dynamic_values

            ctx["columns"] = columns
            ctx["status_choices"] = ApplicationStatus.choices
            ctx["current_status"] = status_filter
            ctx["can_manage_applicants"] = has_teamshifts_permission(
                self.request.user, self.request.organizer, event, "can_teamshifts_manage_applicants", request=self.request
            )
            ctx["search"] = search
        return ctx


class ApplicationStatusView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            application = get_object_or_404(
                TeamMemberApplication.objects.select_related("user"),
                pk=kwargs["pk"],
                event=event,
            )
            new_status = request.POST.get("status")
            if new_status not in ApplicationStatus.values:
                return HttpResponseBadRequest("Invalid status")
            old_status = application.status
            if new_status != old_status:
                application.status = new_status
                application.save(update_fields=["status", "updated_at"])
                if new_status == ApplicationStatus.ACCEPTED:
                    messages.success(request, _("Application by %s accepted.") % application.user.email)
                    transaction.on_commit(lambda app=application: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_ACCEPTED))
                elif new_status == ApplicationStatus.REJECTED:
                    messages.warning(request, _("Application by %s rejected.") % application.user.email)
                    transaction.on_commit(lambda app=application: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_REJECTED))
                else:
                    messages.success(request, _("Application status updated to %s.") % application.get_status_display())

            next_url = request.GET.get("next") or request.POST.get("next")
            if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)

        return redirect(reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug}))


class BulkApplicationStatusView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_manage_applicants"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            action = request.POST.get("action")
            if action not in ("accept", "reject"):
                return HttpResponseBadRequest("Invalid action")

            app_ids = request.POST.getlist("application_ids")
            if not app_ids:
                messages.warning(request, _("No applications selected."))
                return redirect(reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug}))

            new_status = ApplicationStatus.ACCEPTED if action == "accept" else ApplicationStatus.REJECTED

            apps = list(
                TeamMemberApplication.objects.filter(
                    event=event,
                    pk__in=app_ids,
                )
                .exclude(status=new_status)
                .select_related("user")
            )

            if not apps:
                messages.warning(request, _("The selected applications already have this status."))
                return redirect(reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug}))

            TeamMemberApplication.objects.filter(
                event=event,
                pk__in=[a.pk for a in apps],
            ).update(status=new_status, updated_at=now())

            for app in apps:
                app.status = new_status

            if action == "accept":
                messages.success(request, _("%(count)d applications accepted and emails queued.") % {"count": len(apps)})
                for app in apps:
                    transaction.on_commit(lambda app=app: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_ACCEPTED))
            else:
                messages.warning(request, _("%(count)d applications rejected and emails queued.") % {"count": len(apps)})
                for app in apps:
                    transaction.on_commit(lambda app=app: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_REJECTED))

        return redirect(reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug}))


class ApplicationDetailView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = None
    template_name = "teamshifts/application_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            app = get_object_or_404(
                TeamMemberApplication.objects.select_related("user").prefetch_related(
                    "answers__question",
                    Prefetch(
                        "user__shift_assignments",
                        queryset=ShiftAssignment.objects.filter(shift__event=event).select_related("role"),
                        to_attr="event_assignments",
                    ),
                ),
                pk=kwargs["pk"],
                event=event,
            )
            app.rendered_answers = [{"question": a.question, "value": render_answer_for_review(a.question, a.answer)} for a in app.answers.all()]
            ctx["application"] = app
            ctx["status_choices"] = ApplicationStatus.choices
            ctx["can_view_email"] = can_view_email_addresses(self.request.user, self.request.organizer, event, request=self.request)
            ctx["can_manage_applicants"] = has_teamshifts_permission(
                self.request.user, self.request.organizer, event, "can_teamshifts_manage_applicants", request=self.request
            )
        return ctx


class PublicApplyView(FormView):
    template_name = "teamshifts/apply.html"

    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        if not request.user.is_authenticated:
            login_url = reverse(
                "cfp:event.login",
                kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
            )
            return redirect(f"{login_url}?next={request.get_full_path()}")
        self.event = request.event
        self.organizer = request.organizer
        with scope(event=self.event):
            try:
                self.cfm = self.event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                self.cfm = None
        if self.cfm and self.cfm.cfm_private:
            if not getattr(request, "_cfm_secret_verified", False):
                with scope(event=self.event):
                    has_application = TeamMemberApplication.objects.filter(event=self.event, user=request.user).exists()
                if not has_application:
                    raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        kwargs = self.get_form_kwargs()
        kwargs["event"] = self.event
        kwargs["user"] = self.request.user
        kwargs["cfm"] = self.cfm
        return TeamMemberApplicationForm(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        ctx["cfm"] = self.cfm
        ctx["cfm_open"] = self.cfm is not None and self.cfm.is_open
        ctx["cfm_deadline_passed"] = self.cfm is not None and self.cfm.active and self.cfm.deadline is not None and not self.cfm.is_open
        with scope(event=self.event):
            ctx["existing_application"] = TeamMemberApplication.objects.filter(event=self.event, user=self.request.user).first()
        return ctx

    def form_valid(self, form):
        event = self.event
        if self.cfm is None or not self.cfm.is_open:
            messages.error(self.request, _("Applications are not currently open for this event."))
            return self.form_invalid(form)
        full_name = form.cleaned_data.get("full_name", "").strip()
        with scope(event=event):
            if TeamMemberApplication.objects.filter(event=event, user=self.request.user).exists():
                messages.error(self.request, _("You have already submitted an application for this event."))
                return self.form_invalid(form)

            application = TeamMemberApplication.objects.create(
                event=event,
                user=self.request.user,
                availability_notes=form.cleaned_data.get("availability_notes", ""),
                phone=form.cleaned_data.get("phone", ""),
            )

            for question, answer_text in form.get_question_answers():
                TeamApplicationAnswer.objects.create(application=application, question=question, answer=answer_text)
        if full_name and full_name != self.request.user.fullname:
            self.request.user.fullname = full_name
            self.request.user.save(update_fields=["fullname"])
        transaction.on_commit(lambda app=application: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_RECEIVED))
        messages.success(self.request, _("Your application has been submitted."))
        return redirect(
            reverse(
                "plugins:teamshifts:apply_thanks",
                kwargs={"organizer": self.organizer.slug, "event": event.slug},
            )
        )


class PublicApplyThanksView(TemplateView):
    template_name = "teamshifts/apply_thanks.html"

    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        if not request.user.is_authenticated:
            login_url = reverse(
                "cfp:event.login",
                kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
            )
            return redirect(f"{login_url}?next={request.get_full_path()}")
        self.event = request.event
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        return ctx


class PublicApplySecretView(PublicApplyView):
    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        event = request.event
        with scope(event=event):
            try:
                cfm = event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                raise Http404 from None
        secret = kwargs.get("secret", "")
        if not cfm.active or not cfm.cfm_private or not secret or not secrets.compare_digest(secret, cfm.cfm_secret or ""):
            raise Http404
        request._cfm_secret_verified = True
        return super().dispatch(request, *args, **kwargs)


class EmailComposeView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, FormView):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/emails/compose.html"
    form_class = EmailComposeForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["event"] = self.request.event
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        copy_pk = self.request.GET.get("copy")
        if copy_pk and copy_pk.isdigit():
            with scope(event=self.request.event):
                try:
                    source = TeamShiftsEmailQueue.objects.get(pk=int(copy_pk), event=self.request.event)
                except TeamShiftsEmailQueue.DoesNotExist:
                    return initial
            initial["subject"] = source.subject
            initial["message"] = source.message
            initial["status"] = source.status_filter or ""
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preview_recipients"] = getattr(self, "_preview_recipients", None)
        ctx["can_view_email"] = can_view_email_addresses(self.request.user, self.request.organizer, self.request.event, request=self.request)
        return ctx

    def form_invalid(self, form):
        messages.error(self.request, _("Please correct the errors below."))
        return super().form_invalid(form)

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "preview":
            event = request.event
            status = request.POST.get("status") or ""
            recipients = get_recipients(event, status=status)
            self._preview_recipients = recipients
            locales = list(event.settings.get("locales") or [event.settings.locale])
            subject_i18n = LazyI18nString({locales[i]: request.POST.get(f"subject_{i}", "") for i in range(len(locales))})
            message_i18n = LazyI18nString({locales[i]: request.POST.get(f"message_{i}", "") for i in range(len(locales))})
            form = EmailComposeForm(
                event=event,
                initial={
                    "status": status,
                    "subject": subject_i18n,
                    "message": message_i18n,
                    "send_after": request.POST.get("send_after", ""),
                },
            )
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        event = self.request.event
        status = form.cleaned_data.get("status") or ""
        send_after = form.cleaned_data.get("send_after")

        recipients = get_recipients(event, status=status)

        action = self.request.POST.get("action")
        if action == "preview":
            self._preview_recipients = recipients
            return self.render_to_response(self.get_context_data(form=form))

        if not recipients:
            messages.error(self.request, _("No recipients match the selected filters."))
            return self.render_to_response(self.get_context_data(form=form))

        queue_email(
            event=event,
            subject=form.cleaned_data["subject"],
            message=form.cleaned_data["message"],
            recipients=recipients,
            user=self.request.user,
            status_filter=status,
            send_after=send_after,
            dispatch=(action != "draft"),
        )
        if send_after:
            messages.success(
                self.request,
                ngettext(
                    "Email scheduled for %(count)d recipient. It stays in the outbox until %(when)s.",
                    "Email scheduled for %(count)d recipients. It stays in the outbox until %(when)s.",
                    len(recipients),
                )
                % {
                    "count": len(recipients),
                    "when": date_format(send_after, "SHORT_DATETIME_FORMAT"),
                },
            )
        elif action == "draft":
            messages.success(
                self.request,
                ngettext(
                    "Email saved to outbox for %(count)d recipient.",
                    "Email saved to outbox for %(count)d recipients.",
                    len(recipients),
                )
                % {"count": len(recipients)},
            )
        else:
            messages.success(
                self.request,
                ngettext(
                    "Email sent to %(count)d recipient.",
                    "Email sent to %(count)d recipients.",
                    len(recipients),
                )
                % {"count": len(recipients)},
            )
        if action == "send" and not send_after:
            return redirect(
                "plugins:teamshifts:email_sent",
                organizer=self.request.organizer.slug,
                event=event.slug,
            )
        return redirect(
            "plugins:teamshifts:email_outbox",
            organizer=self.request.organizer.slug,
            event=event.slug,
        )


class EmailOutboxView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/emails/outbox_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            queues = list(
                TeamShiftsEmailQueue.objects.filter(event=event, sent_at__isnull=True, user__isnull=False)
                .select_related("role_filter", "user")
                .annotate(recipient_count=Count("recipients"))
                .order_by("-created")
            )
        ctx["mails"] = queues
        ctx["can_view_email"] = can_view_email_addresses(self.request.user, self.request.organizer, event, request=self.request)
        return ctx


class EmailSentView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/emails/sent_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            queues = list(
                TeamShiftsEmailQueue.objects.filter(event=event, sent_at__isnull=False, user__isnull=False)
                .select_related("role_filter", "user")
                .annotate(recipient_count=Count("recipients"))
                .order_by("-sent_at")
            )
        ctx["mails"] = queues
        ctx["can_view_email"] = can_view_email_addresses(self.request.user, self.request.organizer, event, request=self.request)
        return ctx


class EmailQueueEditView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/emails/outbox_form.html"

    def _get_queue(self):
        with scope(event=self.request.event):
            queue = get_object_or_404(
                TeamShiftsEmailQueue.objects.select_related("role_filter"),
                pk=self.kwargs["pk"],
                event=self.request.event,
            )
        return queue

    def get(self, request, *args, **kwargs):
        queue = self._get_queue()
        form = EmailQueueEditForm(instance=queue, event=request.event)
        return render(
            request,
            self.template_name,
            {"form": form, "queue": queue, "can_view_email": can_view_email_addresses(request.user, request.organizer, request.event, request=request)},
        )

    def post(self, request, *args, **kwargs):
        queue = self._get_queue()
        if queue.sent_at:
            messages.error(request, _("This email has already been sent and cannot be edited."))
            return redirect(
                "plugins:teamshifts:email_sent",
                organizer=request.organizer.slug,
                event=request.event.slug,
            )
        form = EmailQueueEditForm(request.POST, instance=queue, event=request.event)
        if form.is_valid():
            with scope(event=request.event):
                queue = form.save()

            if "send_after" in form.changed_data and not queue.send_after:
                from .services.email import _dispatch

                _dispatch(request.event.pk, queue.pk, eta=None)
                messages.success(request, _("Email queued for immediate sending."))
            else:
                messages.success(request, _("Email saved."))

            return redirect(
                "plugins:teamshifts:email_outbox",
                organizer=request.organizer.slug,
                event=request.event.slug,
            )
        return render(
            request,
            self.template_name,
            {"form": form, "queue": queue, "can_view_email": can_view_email_addresses(request.user, request.organizer, request.event, request=request)},
        )


class EmailQueueDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/emails/delete_confirmation.html"

    def _get_queue(self):
        with scope(event=self.request.event):
            return get_object_or_404(
                TeamShiftsEmailQueue,
                pk=self.kwargs["pk"],
                event=self.request.event,
            )

    def get(self, request, *args, **kwargs):
        queue = self._get_queue()
        return render(request, self.template_name, {"queue": queue})

    def post(self, request, *args, **kwargs):
        queue = self._get_queue()
        if queue.sent_at:
            messages.error(request, _("This email has already been sent and cannot be deleted."))
        else:
            with scope(event=request.event):
                queue.delete()
            messages.success(request, _("Email deleted."))
        return redirect(
            "plugins:teamshifts:email_sent" if queue.sent_at else "plugins:teamshifts:email_outbox",
            organizer=request.organizer.slug,
            event=request.event.slug,
        )


class EmailQueueSendNowView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            queue = get_object_or_404(TeamShiftsEmailQueue, pk=kwargs["pk"], event=event)
            if queue.sent_at:
                messages.warning(request, _("This email has already been sent."))
            else:
                queue.send_after = None
                queue.save(update_fields=["send_after", "updated"])
                transaction.on_commit(lambda: send_queued_email.delay(event.pk, queue.pk))
                messages.success(request, _("Email queued for sending."))
        return redirect(
            "plugins:teamshifts:email_outbox",
            organizer=request.organizer.slug,
            event=event.slug,
        )


class ShiftLocationListView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/locations.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            locations = list(ShiftLocation.objects.filter(event=request.event))
        return render(request, self.template_name, {"locations": locations})


class ShiftLocationCreateView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/location_edit.html"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {"form": ShiftLocationForm()})

    def post(self, request, *args, **kwargs):
        form = ShiftLocationForm(request.POST)
        form.instance.event = request.event
        with scope(event=request.event):
            is_valid = form.is_valid()
            if is_valid:
                location = form.save()
        if is_valid:
            messages.success(request, _("Location '%s' created.") % location.name)
            return redirect("plugins:teamshifts:locations", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form})


class ShiftLocationUpdateView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/location_edit.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            location = get_object_or_404(ShiftLocation, pk=kwargs["pk"], event=request.event)
        form = ShiftLocationForm(instance=location)
        return render(request, self.template_name, {"form": form, "location": location})

    def post(self, request, *args, **kwargs):
        with scope(event=request.event):
            location = get_object_or_404(ShiftLocation, pk=kwargs["pk"], event=request.event)
        form = ShiftLocationForm(request.POST, instance=location)
        with scope(event=request.event):
            is_valid = form.is_valid()
            if is_valid:
                form.save()
        if is_valid:
            messages.success(request, _("Location '%s' updated.") % location.name)
            return redirect("plugins:teamshifts:locations", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form, "location": location})


class ShiftLocationDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/location_delete.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            location = get_object_or_404(ShiftLocation, pk=kwargs["pk"], event=request.event)
        return render(request, self.template_name, {"location": location})

    def post(self, request, *args, **kwargs):
        with scope(event=request.event):
            location = get_object_or_404(ShiftLocation, pk=kwargs["pk"], event=request.event)
            if location.shifts.exists():
                messages.error(request, _("Cannot delete '%s': it is used by existing shifts.") % location.name)
            else:
                name = location.name
                location.delete()
                messages.success(request, _("Location '%s' deleted.") % name)
        return redirect("plugins:teamshifts:locations", organizer=request.organizer.slug, event=request.event.slug)


class ShiftListView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, PaginationMixin, ListView):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/shifts.html"
    context_object_name = "shifts"
    DEFAULT_PAGINATION = 50

    def get_queryset(self):
        event = self.request.event
        with scope(event=event):
            return Shift.objects.filter(event=event).select_related("location").prefetch_related("role_assignments__role").order_by("start_time", "pk")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["can_manage_shifts"] = has_teamshifts_permission(
            self.request.user, self.request.organizer, event, "can_teamshifts_create_shifts", request=self.request
        )
        return ctx


class BulkShiftDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            shift_ids = [int(v) for v in request.POST.getlist("shift_ids") if str(v).strip().isdigit()]
            if not shift_ids:
                messages.warning(request, _("No shifts selected."))
                return redirect(
                    reverse(
                        "plugins:teamshifts:shifts",
                        kwargs={"organizer": event.organizer.slug, "event": event.slug},
                    )
                )

            with transaction.atomic():
                shifts = Shift.objects.filter(event=event, pk__in=shift_ids)
                count = shifts.count()
                if count == 0:
                    messages.warning(request, _("No shifts selected."))
                    return redirect(
                        reverse(
                            "plugins:teamshifts:shifts",
                            kwargs={"organizer": event.organizer.slug, "event": event.slug},
                        )
                    )
                shifts.delete()

            messages.success(
                request,
                ngettext(
                    "%(count)d shift deleted.",
                    "%(count)d shifts deleted.",
                    count,
                )
                % {"count": count},
            )
            return redirect(
                reverse(
                    "plugins:teamshifts:shifts",
                    kwargs={"organizer": event.organizer.slug, "event": event.slug},
                )
            )


class ShiftCreateView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/shift_create.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        with scope(event=self.request.event):
            ctx["has_locations"] = kwargs.get("has_locations", ShiftLocation.objects.filter(event=self.request.event).exists())

        ctx["form"] = kwargs.get("form") or (
            ShiftForm(self.request.POST, event=self.request.event) if self.request.method == "POST" else ShiftForm(event=self.request.event)
        )
        ctx["formset"] = kwargs.get("formset") or (
            ShiftRoleFormSet(self.request.POST, prefix="roles", form_kwargs={"event": self.request.event})
            if self.request.method == "POST"
            else ShiftRoleFormSet(prefix="roles", form_kwargs={"event": self.request.event})
        )
        return ctx

    def post(self, request, *args, **kwargs):
        with scope(event=self.request.event):
            has_locations = ShiftLocation.objects.filter(event=self.request.event).exists()

        if not has_locations:
            messages.error(request, _("No locations defined yet, add locations first."))
            return redirect("plugins:teamshifts:location_create", organizer=request.event.organizer.slug, event=request.event.slug)

        form = ShiftForm(self.request.POST, event=self.request.event)
        formset = ShiftRoleFormSet(self.request.POST, prefix="roles", form_kwargs={"event": self.request.event})

        if form.is_valid() and formset.is_valid():
            with scope(event=request.event), transaction.atomic():
                mode = form.cleaned_data.get("mode")
                shifts_to_create = []

                if mode == "repeating":
                    shift_length = form.cleaned_data["shift_length_minutes"]
                    curr_start = form.cleaned_data["start_time"]
                    end_time = form.cleaned_data["end_time"]

                    while curr_start < end_time:
                        curr_end = curr_start + timedelta(minutes=shift_length)
                        if curr_end > end_time:
                            break

                        shift = Shift(
                            event=self.request.event,
                            name=form.cleaned_data.get("name", ""),
                            location=form.cleaned_data["location"],
                            start_time=curr_start,
                            end_time=curr_end,
                            description=form.cleaned_data.get("description", ""),
                        )
                        shift.save()
                        shifts_to_create.append(shift)
                        curr_start = curr_end

                else:
                    shift = form.save(commit=False)
                    shift.event = self.request.event
                    shift.save()
                    shifts_to_create.append(shift)

                assignments_to_create = []
                for shift in shifts_to_create:
                    for role_form in formset:
                        if role_form.cleaned_data and not role_form.cleaned_data.get("DELETE", False):
                            role = role_form.cleaned_data.get("role")
                            capacity = role_form.cleaned_data.get("capacity", 1)
                            if role:
                                assignments_to_create.append(ShiftRoleAssignment(shift=shift, role=role, capacity=capacity))
                if assignments_to_create:
                    ShiftRoleAssignment.objects.bulk_create(assignments_to_create)
            if mode == "repeating":
                messages.success(request, _("%(count)d shifts created successfully.") % {"count": len(shifts_to_create)})
            else:
                messages.success(request, _("Shift created successfully."))
            return redirect(
                "plugins:teamshifts:shifts",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
            )

        ctx = self.get_context_data(form=form, formset=formset, has_locations=has_locations)
        messages.error(request, _("We could not save your changes. See below for details."))
        return self.render_to_response(ctx)


class ShiftUpdateView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/shift_edit.html"

    def dispatch(self, request, *args, **kwargs):
        self.shift = get_object_or_404(Shift, pk=kwargs.get("pk"), event=request.event)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        with scope(event=self.request.event):
            ctx["has_locations"] = kwargs.get("has_locations", ShiftLocation.objects.filter(event=self.request.event).exists())

        ctx["form"] = kwargs.get("form") or (
            ShiftForm(self.request.POST, event=self.request.event, instance=self.shift)
            if self.request.method == "POST"
            else ShiftForm(event=self.request.event, instance=self.shift)
        )
        ctx["formset"] = kwargs.get("formset") or (
            ShiftRoleFormSet(self.request.POST, prefix="roles", instance=self.shift, form_kwargs={"event": self.request.event})
            if self.request.method == "POST"
            else ShiftRoleFormSet(prefix="roles", instance=self.shift, form_kwargs={"event": self.request.event})
        )
        return ctx

    def post(self, request, *args, **kwargs):
        with scope(event=self.request.event):
            has_locations = ShiftLocation.objects.filter(event=self.request.event).exists()

        if not has_locations:
            messages.error(request, _("No locations defined yet, add locations first."))
            return redirect("plugins:teamshifts:location_create", organizer=request.event.organizer.slug, event=request.event.slug)

        form = ShiftForm(self.request.POST, event=self.request.event, instance=self.shift)
        formset = ShiftRoleFormSet(self.request.POST, prefix="roles", instance=self.shift, form_kwargs={"event": self.request.event})

        form_valid = form.is_valid()
        formset_valid = formset.is_valid()

        if form_valid and formset_valid:
            with scope(event=request.event), transaction.atomic():
                form.save()
                formset.save()
                messages.success(request, _("Shift updated successfully."))
                return redirect("plugins:teamshifts:shift_edit", organizer=request.event.organizer.slug, event=request.event.slug, pk=self.shift.pk)
        else:
            messages.error(request, _("We could not save your changes. See below for details."))
            ctx = self.get_context_data(form=form, formset=formset, has_locations=has_locations)
            return self.render_to_response(ctx)


class ShiftDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, DeleteView):
    model = Shift
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/shift_delete.html"
    context_object_name = "shift"

    def get_object(self, queryset=None):
        return get_object_or_404(Shift, pk=self.kwargs.get("pk"), event=self.request.event)

    def get_success_url(self):
        return reverse(
            "plugins:teamshifts:shifts",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )

    def delete(self, request, *args, **kwargs):
        messages.success(request, _("The shift has been deleted."))
        return super().delete(request, *args, **kwargs)


class MembersListView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, PaginationMixin, ListView):
    permission = None
    template_name = "teamshifts/members.html"
    context_object_name = "members"

    def get_queryset(self):
        event = self.request.event
        with scope(event=event):
            qs = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).select_related("user")

            search = self.request.GET.get("q", "").strip()
            if search:
                can_view_email = can_view_email_addresses(self.request.user, self.request.organizer, event, request=self.request)
                if can_view_email:
                    qs = qs.filter(Q(user__email__icontains=search) | Q(user__fullname__icontains=search))
                else:
                    qs = qs.filter(Q(user__fullname__icontains=search))

            qs = (
                qs.annotate(
                    shifts_assigned=Count("user__shift_assignments", filter=Q(user__shift_assignments__shift__event=event)),
                    hours_scheduled=Sum(
                        ExpressionWrapper(
                            F("user__shift_assignments__shift__end_time") - F("user__shift_assignments__shift__start_time"),
                            output_field=DurationField(),
                        ),
                        filter=Q(user__shift_assignments__shift__event=event),
                    ),
                )
                .prefetch_related(
                    Prefetch(
                        "user__shift_assignments",
                        queryset=ShiftAssignment.objects.filter(shift__event=event).select_related("role"),
                        to_attr="event_assignments",
                    ),
                )
                .order_by("user__fullname", "user__email")
            )

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            ctx["roles"] = list(TeamRole.objects.filter(event=event))

        ctx["can_view_email"] = can_view_email_addresses(self.request.user, self.request.organizer, self.request.event, request=self.request)
        ctx["can_add_member"] = has_teamshifts_permission(
            self.request.user,
            self.request.organizer,
            self.request.event,
            "can_teamshifts_manage_applicants",
            request=self.request,
        )
        return ctx


class MemberCreateView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, FormView):
    permission = "can_teamshifts_manage_applicants"
    template_name = "teamshifts/member_add.html"
    form_class = TeamMemberApplicationForm

    def _get_cfm(self):
        with scope(event=self.request.event):
            cfm, _created = CallForTeamMembers.objects.get_or_create(event=self.request.event)
        return cfm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["event"] = self.request.event
        kwargs["cfm"] = self._get_cfm()
        kwargs["organizer_mode"] = True
        return kwargs

    def form_valid(self, form):
        try:
            application = add_member_from_organizer(event=self.request.event, form=form)
        except AlreadyMemberError:
            form.add_error("email", _("This person is already an accepted team member for this event."))
            return self.form_invalid(form)

        transaction.on_commit(lambda app=application: queue_lifecycle_email(app, EmailTemplateRoles.MEMBER_ADDED_BY_ORGANIZER))
        messages.success(self.request, _("Team member added."))
        return redirect(
            "plugins:teamshifts:members",
            organizer=self.request.organizer.slug,
            event=self.request.event.slug,
        )


class MemberArrivedToggleView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            application = get_object_or_404(TeamMemberApplication, pk=kwargs["pk"], event=event, status=ApplicationStatus.ACCEPTED)
            application.arrived = not application.arrived
            application.save(update_fields=["arrived"])
            maybe_auto_issue_certificate(application)
            return JsonResponse({"success": True, "arrived": application.arrived})


class CustomEmailTemplateCreateView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/custom_email_template_form.html"

    def get(self, request, *args, **kwargs):
        form = CustomEmailTemplateForm(locales=request.event.settings.locales)
        return render(request, self.template_name, {"form": form})

    def post(self, request, *args, **kwargs):
        form = CustomEmailTemplateForm(request.POST, locales=request.event.settings.locales)
        if form.is_valid():
            template = form.save(commit=False)
            template.event = request.event
            with scope(event=request.event):
                template.save()
            messages.success(request, _("Template created."))
            return redirect(
                "plugins:teamshifts:email_templates",
                organizer=request.organizer.slug,
                event=request.event.slug,
            )
        return render(request, self.template_name, {"form": form})


class CustomEmailTemplateDeleteView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_send_emails"
    template_name = "teamshifts/custom_email_template_delete.html"

    def _get_template(self, request, pk):
        with scope(event=request.event):
            return get_object_or_404(TeamShiftsCustomEmailTemplate, pk=pk, event=request.event)

    def get(self, request, *args, **kwargs):
        template = self._get_template(request, kwargs["pk"])
        return render(request, self.template_name, {"object": template})

    def post(self, request, *args, **kwargs):
        template = self._get_template(request, kwargs["pk"])
        with scope(event=request.event):
            template.delete()
        messages.success(request, _("Template deleted."))
        return redirect(
            "plugins:teamshifts:email_templates",
            organizer=request.organizer.slug,
            event=request.event.slug,
        )


class ShiftScheduleTalksAPIView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def get(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            data = {
                "version": None,
                "event_start": event.date_from.isoformat() if event.date_from else "",
                "event_end": event.date_to.isoformat() if event.date_to else "",
                "timezone": event.timezone,
                "locales": ["en"],
                "rooms": [],
                "tracks": [],
                "speakers": [],
                "talks": [],
                "warnings": {},
                "roles": [],
            }

            roles = event.team_roles.all()
            for role in roles:
                data["roles"].append({"id": role.id, "name": {"en": role.name}, "is_restricted": role.is_restricted})

            locations = event.shift_locations.all()
            for loc in locations:
                data["rooms"].append({"id": loc.id, "name": {"en": loc.name}, "description": {"en": _html_to_plain(loc.description)}})

            shifts = event.shifts.all().prefetch_related(
                "role_assignments__role",
                "assignments__team_member",
                "assignments__role",
            )
            for shift in shifts:
                roles_data = []
                for role_assignment in shift.role_assignments.all():
                    assignments = []
                    for assignment in shift.assignments.all():
                        if assignment.team_member_id and assignment.role_id == role_assignment.role_id:
                            name = assignment.team_member.get_full_name() or assignment.team_member.email
                            assignments.append({"id": assignment.team_member.id, "name": name})
                    roles_data.append(
                        {
                            "id": role_assignment.role.id,
                            "name": {"en": role_assignment.role.name},
                            "capacity": role_assignment.capacity,
                            "assigned": assignments,
                            "is_restricted": role_assignment.role.is_restricted,
                        }
                    )
                data["talks"].append(
                    {
                        "id": shift.id,
                        "code": str(shift.id),
                        "title": {"en": shift.name or "Shift"},
                        "abstract": "",
                        "description": shift.description,
                        "room": shift.location_id,
                        "start": shift.start_time.isoformat() if shift.start_time else "",
                        "end": shift.end_time.isoformat() if shift.end_time else "",
                        "duration": int((shift.end_time - shift.start_time).total_seconds() / 60) if shift.end_time and shift.start_time else 0,
                        "roles": roles_data,
                        "state": "confirmed",
                    }
                )
            return JsonResponse(data)

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HttpResponseBadRequest("Invalid JSON body.")
        event = request.event
        with scope(event=event):
            if not data.get("start") or not data.get("end"):
                return HttpResponseBadRequest("Both 'start' and 'end' are required.")
            try:
                start = dateutil.parser.parse(data["start"])
                end = dateutil.parser.parse(data["end"])
            except (ValueError, OverflowError):
                return HttpResponseBadRequest("Invalid date format.")
            if end <= start:
                return HttpResponseBadRequest("'end' must be after 'start'.")
            room_id = data.get("room")
            if isinstance(room_id, dict):
                room_id = room_id.get("id")

            location = ShiftLocation.objects.filter(id=room_id, event=event).first() if room_id else None

            title_val = data.get("title", {})
            if isinstance(title_val, dict):
                shift_name = title_val.get("en", "Shift")
            elif isinstance(title_val, str):
                shift_name = title_val
            else:
                shift_name = "Shift"

            shift = Shift.objects.create(
                event=event,
                name=shift_name,
                description=data.get("description", ""),
                location=location,
                start_time=start,
                end_time=end,
            )

            for role_data in data.get("roles", []):
                role_id = role_data.get("id")
                capacity = role_data.get("capacity", 1)
                if role_id:
                    role = TeamRole.objects.filter(pk=role_id, event=event).first()
                    if not role:
                        return HttpResponseBadRequest("Role ID does not belong to this event.")
                    ShiftRoleAssignment.objects.create(shift=shift, role=role, capacity=capacity)

            return JsonResponse({"id": shift.id})


class ShiftScheduleTalkAPIView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def patch(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HttpResponseBadRequest("Invalid JSON body.")
        event = request.event
        with scope(event=event):
            shift = get_object_or_404(Shift, pk=kwargs["pk"], event=event)

            if data.get("start"):
                try:
                    shift.start_time = dateutil.parser.parse(data["start"])
                    if data.get("end"):
                        shift.end_time = dateutil.parser.parse(data["end"])
                    elif shift.start_time and shift.end_time:
                        duration = (shift.end_time - shift.start_time).total_seconds() / 60
                        shift.end_time = shift.start_time + timedelta(minutes=duration)
                    else:
                        shift.end_time = shift.start_time + timedelta(minutes=data.get("duration", 60) or 60)
                except (TypeError, ValueError, OverflowError):
                    return HttpResponseBadRequest("Invalid date format for 'start'/'end'.")

                if "room" in data:
                    room_id = data["room"]
                    if isinstance(room_id, dict):
                        room_id = room_id.get("id")
                    shift.location = ShiftLocation.objects.filter(id=room_id, event=event).first() if room_id else None

                if shift.start_time and shift.end_time and shift.end_time <= shift.start_time:
                    return HttpResponseBadRequest("'end' must be after 'start'.")
            else:
                shift.location = None

            if "title" in data:
                title_val = data["title"]
                if isinstance(title_val, dict):
                    shift.name = title_val.get("en", shift.name)
                elif isinstance(title_val, str):
                    shift.name = title_val
            if "description" in data:
                shift.description = data["description"]

            shift.save()

            if "roles" in data:
                incoming_role_ids = set()
                for role_data in data["roles"]:
                    role_id = role_data.get("id")
                    if role_id:
                        incoming_role_ids.add(role_id)
                valid_roles = TeamRole.objects.filter(pk__in=incoming_role_ids, event=event)
                valid_role_ids = set(valid_roles.values_list("pk", flat=True))
                invalid_ids = incoming_role_ids - valid_role_ids
                if invalid_ids:
                    return HttpResponseBadRequest("One or more role IDs do not belong to this event.")
                shift.assignments.exclude(role_id__in=incoming_role_ids).delete()
                shift.role_assignments.all().delete()
                for role_data in data["roles"]:
                    role_id = role_data.get("id")
                    capacity = role_data.get("capacity", 1)
                    if role_id:
                        ShiftRoleAssignment.objects.create(shift=shift, role_id=role_id, capacity=capacity)
            elif "role" in data and "capacity" in data:
                role_id = data["role"]
                if role_id:
                    if not TeamRole.objects.filter(pk=role_id, event=event).exists():
                        return HttpResponseBadRequest("Role ID does not belong to this event.")
                if role_id:
                    shift.assignments.exclude(role_id=role_id).delete()
                else:
                    shift.assignments.all().delete()
                shift.role_assignments.all().delete()
                if role_id:
                    ShiftRoleAssignment.objects.create(shift=shift, role_id=role_id, capacity=data.get("capacity", 1))

            return JsonResponse({"status": "ok"})

    def delete(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            shift = get_object_or_404(Shift, pk=kwargs["pk"], event=event)
            shift.delete()
            return JsonResponse({"status": "ok"})


class ShiftScheduleMembersAPIView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def get(self, request, *args, **kwargs):
        event = request.event
        show_email = can_view_email_addresses(request.user, request.organizer, event, request=request)
        with scope(event=event):
            apps = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).select_related("user")
            members = []
            for app in apps:
                if app.user:
                    name = app.user.get_full_name() or app.user.email
                    member = {"id": app.user.id, "name": name}
                    if show_email:
                        member["email"] = app.user.email
                    members.append(member)
            return JsonResponse({"members": members})


class ShiftScheduleAssignmentsAPIView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HttpResponseBadRequest("Invalid JSON body.")
        event = request.event
        with scope(event=event):
            shift_id = data.get("shift_id")
            user_id = data.get("user_id")
            role_id = data.get("role_id")

            shift = get_object_or_404(Shift, pk=shift_id, event=event)

            if not TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED, user_id=user_id).exists():
                return HttpResponseBadRequest("User is not an accepted team member for this event.")
            user = get_object_or_404(User, pk=user_id)

            if role_id and not shift.role_assignments.filter(role_id=role_id).exists():
                return HttpResponseBadRequest("Role is not configured for this shift.")

            # Capacity check: ensure assignment won't exceed role capacity
            if role_id:
                role_assignment = shift.role_assignments.filter(role_id=role_id).first()
                if role_assignment:
                    current_count = ShiftAssignment.objects.filter(shift=shift, role_id=role_id).exclude(team_member=user).count()
                    if current_count >= role_assignment.capacity:
                        return HttpResponseBadRequest("Role capacity has been reached for this shift.")

            if shift.start_time and shift.end_time:
                conflicting = (
                    ShiftAssignment.objects.filter(
                        team_member=user,
                        shift__event=event,
                        shift__start_time__lt=shift.end_time,
                        shift__end_time__gt=shift.start_time,
                    )
                    .exclude(shift=shift)
                    .select_related("shift")
                    .first()
                )
                if conflicting:
                    return HttpResponseBadRequest("Member is already assigned to another shift during this time.")

            ShiftAssignment.objects.update_or_create(
                shift=shift,
                team_member=user,
                defaults={"role_id": role_id, "assigned_by": request.user},
            )
            return JsonResponse({"status": "ok"})

    def delete(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            shift_id = request.GET.get("shift_id")
            user_id = request.GET.get("user_id")
            role_id = request.GET.get("role_id")

            shift = get_object_or_404(Shift, pk=shift_id, event=event)
            assignment = ShiftAssignment.objects.filter(shift=shift, team_member_id=user_id, role_id=role_id).first()
            if assignment:
                assignment.delete()
            return JsonResponse({"status": "ok"})


class ShiftScheduleAvailabilitiesAPIView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def get(self, request, *args, **kwargs):
        return JsonResponse({"rooms": {}, "talks": {}})


class ShiftScheduleWarningsAPIView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def get(self, request, *args, **kwargs):
        return JsonResponse({})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ShiftScheduleGridEditorView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, TemplateView):
    permission = "can_teamshifts_create_shifts"
    template_name = "teamshifts/schedule_grid.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        language_information = get_language_info(get_language())
        path = language_information.get("path", language_information.get("code", "en"))
        ctx["gettext_language"] = path.replace("-", "_")

        with scope(event=self.request.event):
            cfm = getattr(self.request.event, "call_for_team_members", None)
            ctx["shift_schedule_published"] = cfm.shift_schedule_published if cfm else False
        return ctx


class ShiftScheduleTogglePublishView(PluginActiveMixin, TeamShiftsPermissionRequiredMixin, View):
    permission = "can_teamshifts_create_shifts"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            cfm, _created = CallForTeamMembers.objects.get_or_create(event=event)
            cfm.shift_schedule_published = not cfm.shift_schedule_published
            cfm.save(update_fields=["shift_schedule_published"])
            if cfm.shift_schedule_published:
                messages.success(request, _("Shift schedule has been published. Team members can now view it."))
            else:
                messages.success(request, _("Shift schedule has been unpublished. Team members can no longer view it."))
        return redirect(
            reverse(
                "plugins:teamshifts:schedule_grid",
                kwargs={"organizer": request.organizer.slug, "event": event.slug},
            )
        )


def _get_accepted_application(request, event):
    with scope(event=event):
        return TeamMemberApplication.objects.filter(event=event, user=request.user, status=ApplicationStatus.ACCEPTED).first()


def _public_person_label(user, fallback):
    if not user:
        return fallback
    name = (user.get_full_name() or "").strip()
    return name or fallback


def _shift_roles_payload(shift):
    grouped = {}
    for assignment in shift.assignments.all():
        if not assignment.team_member_id or not assignment.role_id:
            continue
        assigned_by = assignment.assigned_by
        grouped.setdefault(assignment.role_id, []).append(
            {
                "id": assignment.team_member_id,
                "name": _public_person_label(assignment.team_member, str(_("Team member"))),
                "self_assigned": assignment.assigned_by_id is None,
                "assigned_by_name": _public_person_label(assigned_by, str(_("Organizer"))) if assigned_by else None,
            }
        )
    roles_data = []
    for sra in shift.role_assignments.all():
        roles_data.append(
            {
                "id": sra.role_id,
                "name": {"en": sra.role.name},
                "capacity": sra.capacity,
                "assigned": grouped.get(sra.role_id, []),
                "is_restricted": sra.role.is_restricted,
            }
        )
    return roles_data


_BLOCK_TAG_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|dd|details|div|dl|dt|figcaption"
    r"|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|summary"
    r"|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>|<br\b[^>]*>",
    re.IGNORECASE,
)


def _html_to_plain(value: str) -> str:
    """Convert rich-text HTML to plain text for schedule tooltips."""
    if not value:
        return ""
    text = _BLOCK_TAG_RE.sub("\n", value)
    text = strip_tags(text)
    text = html_mod.unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _shift_talk_payload(shift):
    duration = int((shift.end_time - shift.start_time).total_seconds() / 60) if shift.end_time and shift.start_time else 0
    return {
        "id": shift.id,
        "code": str(shift.id),
        "title": {"en": shift.name or "Shift"},
        "abstract": "",
        "description": shift.description or "",
        "speakers": [],
        "track": None,
        "room": shift.location_id,
        "start": shift.start_time.isoformat() if shift.start_time else "",
        "end": shift.end_time.isoformat() if shift.end_time else "",
        "duration": duration,
        "roles": _shift_roles_payload(shift),
        "state": "confirmed",
        "do_not_record": False,
    }


def _public_shifts_queryset(event):
    return event.shifts.filter(
        location__isnull=False,
    ).prefetch_related(
        "role_assignments__role",
        "assignments__team_member",
        "assignments__role",
        "assignments__assigned_by",
    )


def _request_role_id(request):
    role_id = request.POST.get("role_id")
    if role_id is None and request.body:
        try:
            body = json.loads(request.body.decode())
            role_id = body.get("role_id")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            role_id = None
    try:
        return int(role_id)
    except (TypeError, ValueError):
        return None


def _wants_json(request):
    accept = request.headers.get("Accept", "")
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in accept


class PublicShiftScheduleMixin:
    redirect_unpublished_to_schedule = True

    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        if not request.user.is_authenticated:
            login_url = reverse(
                "cfp:event.login",
                kwargs={"organizer": request.organizer.slug, "event": request.event.slug},
            )
            return redirect(f"{login_url}?next={request.get_full_path()}")
        self.event = request.event
        self.organizer = request.organizer
        self.member_application = _get_accepted_application(request, self.event)
        if self.member_application is None:
            messages.error(
                request,
                _("You need to be an accepted team member to view the shift schedule."),
            )
            return redirect(
                reverse(
                    "plugins:teamshifts:apply",
                    kwargs={"organizer": self.organizer.slug, "event": self.event.slug},
                )
            )
        with scope(event=self.event):
            cfm = getattr(self.event, "call_for_team_members", None)
            self.shift_schedule_published = bool(cfm and cfm.shift_schedule_published)
        if not self.shift_schedule_published and self.redirect_unpublished_to_schedule:
            messages.info(
                request,
                _("The shift schedule has not been published yet. Please check back later."),
            )
            return redirect(
                reverse(
                    "plugins:teamshifts:public_shift_schedule",
                    kwargs={"organizer": self.organizer.slug, "event": self.event.slug},
                )
            )
        return super().dispatch(request, *args, **kwargs)


class PublicShiftScheduleAPIView(PublicShiftScheduleMixin, View):
    def get(self, request, *args, **kwargs):
        event = self.event

        with scope(event=event):
            data = {
                "version": None,
                "mode": "shifts",
                "current_user_id": request.user.pk,
                "current_user_name": request.user.get_full_name() or request.user.email,
                "event_start": event.date_from.isoformat() if event.date_from else "",
                "event_end": event.date_to.isoformat() if event.date_to else "",
                "timezone": str(event.timezone),
                "locales": list(event.settings.locales or ["en"]),
                "rooms": [],
                "tracks": [],
                "speakers": [],
                "talks": [],
                "warnings": {},
                "roles": [],
            }

            for role in event.team_roles.all():
                data["roles"].append({"id": role.id, "name": {"en": role.name}, "is_restricted": role.is_restricted})

            for loc in event.shift_locations.all():
                data["rooms"].append(
                    {
                        "id": loc.id,
                        "name": {"en": loc.name},
                        "description": {"en": _html_to_plain(loc.description)},
                    }
                )

            for shift in _public_shifts_queryset(event):
                data["talks"].append(_shift_talk_payload(shift))

        return JsonResponse(data)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PublicShiftScheduleView(PublicShiftScheduleMixin, TemplateView):
    template_name = "teamshifts/shift_schedule.html"
    redirect_unpublished_to_schedule = False

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.event

        if not self.shift_schedule_published:
            ctx.update({"event": event, "shift_schedule_published": False})
            return ctx

        with scope(event=event):
            locations = list(event.shift_locations.all())
            shifts = list(_public_shifts_queryset(event))

        rooms = [
            {
                "id": loc.id,
                "name": {"en": loc.name},
                "description": {"en": _html_to_plain(loc.description)},
            }
            for loc in locations
        ]

        schedule_data = {
            "mode": "shifts",
            "current_user_id": self.request.user.pk,
            "current_user_name": self.request.user.get_full_name() or self.request.user.email,
            "talks": [_shift_talk_payload(shift) for shift in shifts],
            "rooms": rooms,
            "tracks": [],
            "speakers": [],
            "version": None,
            "timezone": str(event.timezone),
            "event_start": event.date_from.isoformat() if event.date_from else "",
            "event_end": event.date_to.isoformat() if event.date_to else "",
            "content_locales": list(event.settings.locales or ["en"]),
            "feature_flags": {},
        }

        json_str = json.dumps(schedule_data, default=str)
        json_escaped = json_str.translate({ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"})

        ctx.update(
            {
                "event": event,
                "event_tz": str(event.timezone),
                "schedule_data_json": json_escaped,
                "shift_schedule_published": True,
            }
        )
        return ctx


class ShiftClaimView(PublicShiftScheduleMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.event
        shift_pk = kwargs["pk"]
        role_id = _request_role_id(request)
        schedule_url = reverse(
            "plugins:teamshifts:public_shift_schedule",
            kwargs={"organizer": self.organizer.slug, "event": event.slug},
        )

        def fail(message, status=400):
            if _wants_json(request):
                return JsonResponse({"status": "error", "error": str(message)}, status=status)
            messages.error(request, message)
            return redirect(schedule_url)

        if role_id is None:
            return fail(_("Select a role to sign up for."))

        with scope(event=event):
            shift = get_object_or_404(Shift, pk=shift_pk, event=event)
            sra = shift.role_assignments.select_related("role").filter(role_id=role_id).first()
            if sra is None:
                return fail(_("This role is not configured for the shift."))
            if sra.role.is_restricted:
                return fail(_("This role requires organizer assignment — you cannot sign up directly."))

            with transaction.atomic():
                Shift.objects.select_for_update().get(pk=shift.pk, event=event)
                sra_locked = ShiftRoleAssignment.objects.select_for_update().get(pk=sra.pk)
                existing = ShiftAssignment.objects.select_for_update().filter(shift=shift, team_member=request.user).first()
                if existing and existing.role_id and existing.role_id != sra.role_id:
                    return fail(_("You are already signed up for another role on this shift."))
                current_count = ShiftAssignment.objects.filter(shift=shift, role_id=sra.role_id).exclude(team_member=request.user).count()
                if current_count >= sra_locked.capacity:
                    return fail(_("Sorry, this role is now full."))
                if shift.start_time and shift.end_time:
                    conflicting = (
                        ShiftAssignment.objects.filter(
                            team_member=request.user,
                            shift__event=event,
                            shift__start_time__lt=shift.end_time,
                            shift__end_time__gt=shift.start_time,
                        )
                        .exclude(shift=shift)
                        .select_related("shift")
                        .first()
                    )
                    if conflicting:
                        return fail(_("You are already assigned to another shift during this time."))
                _assignment, created = ShiftAssignment.objects.update_or_create(
                    shift=shift,
                    team_member=request.user,
                    defaults={"role_id": sra.role_id, "assigned_by": None},
                )
            shift = Shift.objects.prefetch_related(
                "role_assignments__role",
                "assignments__team_member",
                "assignments__role",
            ).get(pk=shift.pk)

        if _wants_json(request):
            return JsonResponse({"status": "ok", "roles": _shift_roles_payload(shift)})
        if created:
            messages.success(request, _("You have been signed up for the shift."))
        else:
            messages.info(request, _("You were already signed up for this shift."))
        return redirect(schedule_url)


class ShiftDetailView(PublicShiftScheduleMixin, TemplateView):
    template_name = "teamshifts/shift_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.event

        with scope(event=event):
            shift = get_object_or_404(Shift, pk=self.kwargs["pk"], event=event)
            assigned_counts_by_role = {
                row["role_id"]: row["assigned_count"]
                for row in ShiftAssignment.objects.filter(shift=shift).values("role_id").annotate(assigned_count=Count("id"))
            }
            role_rows = []
            for sra in shift.role_assignments.select_related("role").all():
                role_rows.append(
                    {
                        "role": sra.role,
                        "capacity": sra.capacity,
                        "assigned_count": assigned_counts_by_role.get(sra.role_id, 0),
                        "is_restricted": sra.role.is_restricted,
                    }
                )

            my_assignment = ShiftAssignment.objects.filter(shift=shift, team_member=self.request.user).first()

        ctx.update(
            {
                "event": event,
                "shift": shift,
                "role_rows": role_rows,
                "my_assignment": my_assignment,
            }
        )
        return ctx


class ShiftWithdrawView(PublicShiftScheduleMixin, View):
    def post(self, request, *args, **kwargs):
        event = self.event
        shift_pk = kwargs["pk"]
        role_id = _request_role_id(request)
        schedule_url = reverse(
            "plugins:teamshifts:public_shift_schedule",
            kwargs={"organizer": self.organizer.slug, "event": event.slug},
        )

        def fail(message, status=400):
            if _wants_json(request):
                return JsonResponse({"status": "error", "error": str(message)}, status=status)
            messages.error(request, message)
            return redirect(schedule_url)

        with scope(event=event):
            shift = get_object_or_404(Shift, pk=shift_pk, event=event)
            assignment_qs = ShiftAssignment.objects.filter(shift=shift, team_member=request.user)
            if role_id is not None:
                assignment_qs = assignment_qs.filter(role_id=role_id)
            assignment = assignment_qs.first()

            if assignment is None:
                return fail(_("You are not signed up for this shift."))

            assignment.delete()
            shift = Shift.objects.prefetch_related(
                "role_assignments__role",
                "assignments__team_member",
                "assignments__role",
            ).get(pk=shift.pk)

        transaction.on_commit(lambda: _notify_organizers_shift_dropped(event, request.user, shift))
        if _wants_json(request):
            return JsonResponse({"status": "ok", "roles": _shift_roles_payload(shift)})
        messages.success(request, _("You have been withdrawn from the shift."))
        return redirect(schedule_url)


def _notify_organizers_shift_dropped(event, volunteer, shift):
    from eventyay.base.models import User

    try:
        cfm = event.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        return

    try:
        template = cfm.get_mail_template(EmailTemplateRoles.SHIFT_DROPPED)
    except Exception:
        logger.exception("Failed to load shift-dropped email template for event %s", event.pk)
        return

    with scopes_disabled():
        organizer_users = list(
            User.objects.filter(
                teams__organizer=event.organizer,
                teams__can_change_event_settings=True,
                teams__all_events=True,
            ).distinct()
        )
        if not organizer_users:
            organizer_users = list(
                User.objects.filter(
                    teams__organizer=event.organizer,
                    teams__limit_events=event,
                    teams__can_change_event_settings=True,
                ).distinct()
            )

    if not organizer_users:
        return

    queue_email(
        event=event,
        subject=template.subject,
        message=template.body,
        recipients=organizer_users,
        status_filter="",
    )


class MyShiftsView(PublicShiftScheduleMixin, TemplateView):
    template_name = "teamshifts/my_shifts.html"
    redirect_unpublished_to_schedule = False

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.event
        with scopes_disabled():
            assignments = (
                ShiftAssignment.objects.filter(
                    team_member=self.request.user,
                    shift__event=event,
                    shift__event__plugins__contains="teamshifts",
                )
                .select_related(
                    "shift",
                    "shift__event",
                    "shift__event__organizer",
                    "shift__location",
                    "role",
                    "assigned_by",
                )
                .order_by("shift__start_time")
            )
        shifts_by_day = defaultdict(list)
        for assignment in assignments:
            local_start = assignment.shift.start_time.astimezone(assignment.shift.event.tz)
            day = local_start.date()
            shifts_by_day[day].append(assignment)
        ctx["shifts_by_day"] = dict(shifts_by_day)
        ctx["event"] = event
        return ctx


class MyShiftsGlobalView(LoginRequiredMixin, TemplateView):
    template_name = "teamshifts/my_shifts_global.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        with scopes_disabled():
            has_active = ShiftAssignment.objects.filter(team_member=request.user, shift__event__plugins__contains="teamshifts").exists()
        if not has_active:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from .forms import MyShiftsFilterForm

        ctx = super().get_context_data(**kwargs)
        filter_form = MyShiftsFilterForm(self.request.GET, user=self.request.user)
        with scopes_disabled():
            qs = (
                ShiftAssignment.objects.filter(
                    team_member=self.request.user,
                    shift__event__plugins__contains="teamshifts",
                )
                .select_related(
                    "shift",
                    "shift__event",
                    "shift__event__organizer",
                    "shift__location",
                    "role",
                    "assigned_by",
                )
                .order_by("shift__event__name", "shift__start_time")
            )

            if filter_form.is_valid():
                event = filter_form.cleaned_data.get("event")
                search = filter_form.cleaned_data.get("search")
                if event:
                    qs = qs.filter(shift__event=event)
                if search:
                    qs = qs.filter(shift__name__icontains=search)

        shifts_by_event = defaultdict(list)
        for assignment in qs:
            shifts_by_event[assignment.shift.event].append(assignment)
        ctx["shifts_by_event"] = dict(shifts_by_event)
        ctx["filter_form"] = filter_form

        event_ids = {e.pk for e in shifts_by_event}
        with scopes_disabled():
            from .models import MemberCertificate

            cert_event_ids = set(
                MemberCertificate.objects.filter(
                    application__user=self.request.user,
                    application__event_id__in=event_ids,
                    file__isnull=False,
                )
                .exclude(file="")
                .values_list("application__event_id", flat=True)
            )
        ctx["cert_event_ids"] = cert_event_ids
        return ctx


class MyShiftsCertificateDownloadView(LoginRequiredMixin, View):
    def get(self, request, event_id):
        with scopes_disabled():
            application = TeamMemberApplication.objects.filter(
                user=request.user,
                event_id=event_id,
                status=ApplicationStatus.ACCEPTED,
                event__plugins__contains="teamshifts",
            ).first()
        if not application:
            raise Http404
        certificate = getattr(application, "certificate", None)
        if not certificate or not certificate.file:
            raise Http404
        response = FileResponse(
            certificate.file.open("rb"),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = f'attachment; filename="certificate-{application.event.slug}.pdf"'
        certificate.downloaded_at = now()
        certificate.save(update_fields=["downloaded_at"])
        return response

import json

from django.conf import settings as django_settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _, ngettext
from django.views.generic import FormView, TemplateView, View
from django_scopes import scope, scopes_disabled
from eventyay.base.i18n import LazyI18nString
from eventyay.base.templatetags.rich_text import rich_text
from eventyay.control.permissions import EventPermissionRequiredMixin

from .forms import (
    CallForTeamMembersApplicationSettingsForm,
    CallForTeamMembersSettingsForm,
    EmailComposeForm,
    EmailQueueEditForm,
    EmailTemplateForm,
    ShiftLocationForm,
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
    ShiftLocation,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailTemplate,
    normalize_field_order,
)
from .services.email import get_recipients, queue_email, queue_lifecycle_email
from .tasks import send_queued_email


class PluginActiveMixin:
    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class TeamShiftsDashboard(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
    template_name = "teamshifts/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            ctx["role_count"] = TeamRole.objects.filter(event=event).count()
            ctx["pending_count"] = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.PENDING).count()
            ctx["accepted_count"] = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).count()
            ctx["shift_count"] = Shift.objects.filter(event=event).count()
            ctx["recent_applications"] = list(TeamMemberApplication.objects.filter(event=event).select_related("user").order_by("-created_at")[:5])
            ctx["accepted_members"] = list(
                TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).select_related("user").order_by("-updated_at")[:8]
            )
            try:
                ctx["cfm"] = event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                ctx["cfm"] = None
        return ctx


class CFMSettingsView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
    template_name = "teamshifts/cfm_settings.html"

    def _get_cfm(self):
        with scope(event=self.request.event):
            obj, _created = CallForTeamMembers.objects.get_or_create(event=self.request.event)
        return obj

    def get(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        form = CallForTeamMembersSettingsForm(instance=cfm, locales=request.event.settings.locales)

        description = cfm.description.data if cfm.description else {}
        if not isinstance(description, dict):
            description = dict.fromkeys(self.request.event.settings.locales, description or "")

        event_locales = set(request.event.settings.locales)
        description_previews = [(code, rich_text(description.get(code, ""))) for code, _name in django_settings.LANGUAGES if code in event_locales]

        return render(request, self.template_name, {"form": form, "cfm": cfm, "description_previews": description_previews})

    def post(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        form = CallForTeamMembersSettingsForm(request.POST, instance=cfm, locales=request.event.settings.locales)
        if form.is_valid():
            with scope(event=request.event):
                form.save()
            messages.success(request, _("Settings saved."))
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)

        description = cfm.description.data if cfm.description else {}
        if not isinstance(description, dict):
            description = dict.fromkeys(request.event.settings.locales, description or "")

        event_locales = set(request.event.settings.locales)
        description_previews = [(code, rich_text(description.get(code, ""))) for code, _name in django_settings.LANGUAGES if code in event_locales]
        return render(request, self.template_name, {"form": form, "cfm": cfm, "description_previews": description_previews})


class CFMApplicationFormView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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

        # Remove 'role' from order if it is present from legacy data
        order = [item for item in order if item != "role"]

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


class CFMDescriptionPreviewView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    """Render draft description text with the same Markdown conversion as the public call page."""

    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        event_locales = set(request.event.settings.locales)
        widget = CallForTeamMembersSettingsForm(locales=list(event_locales)).fields["description"].widget
        raw_values = widget.value_from_datadict(request.POST, request.FILES, "description")
        if not isinstance(raw_values, (list, tuple)):
            raw_values = [raw_values]

        msgs = {}
        for index, (code, _name) in enumerate(django_settings.LANGUAGES):
            if code in event_locales and index < len(raw_values):
                text = raw_values[index]
                msgs[code] = str(rich_text(text)) if text else ""

        return JsonResponse({"msgs": msgs})


class TeamRoleListView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
    template_name = "teamshifts/roles.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            roles = list(TeamRole.objects.filter(event=request.event))
        return render(request, self.template_name, {"roles": roles, "form": TeamRoleForm()})

    def post(self, request, *args, **kwargs):
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
        return render(request, self.template_name, {"roles": roles, "form": form})


class TeamRoleDeleteView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            role = get_object_or_404(TeamRole, pk=kwargs["pk"], event=event)
            if role.shifts.exists():
                messages.error(request, _("Cannot delete '%s': it is used by existing shifts.") % role.name)
            else:
                name = role.name
                role.delete()
                messages.success(request, _("Role '%s' deleted.") % name)
        return redirect("plugins:teamshifts:roles", organizer=request.organizer.slug, event=event.slug)


class EmailTemplateListView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
    template_name = "teamshifts/email_templates.html"

    def get(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            existing = {t.role: t for t in TeamShiftsEmailTemplate.objects.filter(event=event)}
        from .mail.default_templates import get_default_template

        rows = []
        for role in EmailTemplateRoles.values:
            template = existing.get(role)
            default_subject, _ = get_default_template(role)
            rows.append(
                {
                    "role": role,
                    "label": EmailTemplateRoles(role).label,
                    "subject": template.subject if template else default_subject,
                    "is_customised": template is not None,
                }
            )
        return render(request, self.template_name, {"rows": rows})


class EmailTemplateEditView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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


class QuestionEditView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form, "question": instance})


class QuestionDeleteView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

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
        return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=event.slug)


class QuestionReorderView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

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


class QuestionToggleView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

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


class ApplicationListView(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
    template_name = "teamshifts/applications.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            qs = TeamMemberApplication.objects.filter(event=event).select_related("user").prefetch_related("answers__question").order_by("-created_at")
            status_filter = self.request.GET.get("status")
            role_filter = self.request.GET.get("role")
            search = self.request.GET.get("q", "").strip()

            if status_filter in ApplicationStatus.values:
                qs = qs.filter(status=status_filter)
            if role_filter and role_filter.isdigit():
                qs = qs.filter(role_id=int(role_filter))
            else:
                role_filter = ""
            if search:
                qs = qs.filter(Q(user__email__icontains=search) | Q(user__fullname__icontains=search))

            try:
                cfm = event.call_for_team_members
                field_order = normalize_field_order(list(cfm.field_order))
            except CallForTeamMembers.DoesNotExist:
                cfm = None
                field_order = list(CFM_BUILTIN_FIELD_KEYS)

            custom_questions = {str(q.pk): q.question for q in TeamApplicationQuestion.objects.filter(event=event, active=True)}

            active_keys = []
            for k in field_order:
                if k == "role":
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

            applications = list(qs)
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

            ctx["applications"] = applications
            ctx["columns"] = columns
            ctx["roles"] = list(TeamRole.objects.filter(event=event))
            ctx["status_choices"] = ApplicationStatus.choices
            ctx["current_status"] = status_filter
            ctx["current_role"] = role_filter
            ctx["search"] = search
        return ctx


class ApplicationStatusView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            application = get_object_or_404(
                TeamMemberApplication.objects.select_related("user"),
                pk=kwargs["pk"],
                event=event,
            )
            action = request.POST.get("action")
            if action == "accept":
                application.status = ApplicationStatus.ACCEPTED
                application.save(update_fields=["status", "updated_at"])
                messages.success(request, _("Application by %s accepted.") % application.user.email)
                transaction.on_commit(lambda app=application: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_ACCEPTED))
            elif action == "reject":
                application.status = ApplicationStatus.REJECTED
                application.save(update_fields=["status", "updated_at"])
                messages.warning(request, _("Application by %s rejected.") % application.user.email)
                transaction.on_commit(lambda app=application: queue_lifecycle_email(app, EmailTemplateRoles.APPLICATION_REJECTED))
            else:
                raise Http404
        return redirect(reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug}))


class ApplicationDetailView(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
    template_name = "teamshifts/application_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            app = get_object_or_404(
                TeamMemberApplication.objects.select_related("user", "role").prefetch_related("answers__question"),
                pk=kwargs["pk"],
                event=event,
            )
            app.rendered_answers = [{"question": a.question, "value": render_answer_for_review(a.question, a.answer)} for a in app.answers.all()]
            ctx["application"] = app
        return ctx


class PublicApplyView(FormView):
    template_name = "teamshifts/apply.html"

    def dispatch(self, request, *args, **kwargs):
        if "teamshifts" not in request.event.get_plugins():
            raise Http404
        if not request.user.is_authenticated:
            login_url = reverse("eventyay_common:auth.login")
            return redirect(f"{login_url}?next={request.get_full_path()}")
        self.event = request.event
        self.organizer = request.organizer
        with scope(event=self.event):
            try:
                self.cfm = self.event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                self.cfm = None
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
            login_url = reverse("eventyay_common:auth.login")
            return redirect(f"{login_url}?next={request.get_full_path()}")
        self.event = request.event
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        return ctx


class EmailComposeView(PluginActiveMixin, EventPermissionRequiredMixin, FormView):
    permission = "can_change_event_settings"
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
            initial["role"] = source.role_filter_id or None
            initial["status"] = source.status_filter or ""
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preview_recipients"] = getattr(self, "_preview_recipients", None)
        return ctx

    def form_invalid(self, form):
        messages.error(self.request, _("Please correct the errors below."))
        return super().form_invalid(form)

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "preview":
            event = request.event
            role_id = request.POST.get("role") or None
            status = request.POST.get("status") or ""
            role = None
            if role_id:
                with scopes_disabled():
                    role = TeamRole.objects.filter(pk=role_id, event=event).first()
            recipients = get_recipients(event, role=role, status=status)
            self._preview_recipients = recipients
            locales = list(event.settings.get("locales") or [event.settings.locale])
            subject_i18n = LazyI18nString({locales[i]: request.POST.get(f"subject_{i}", "") for i in range(len(locales))})
            message_i18n = LazyI18nString({locales[i]: request.POST.get(f"message_{i}", "") for i in range(len(locales))})
            form = EmailComposeForm(
                event=event,
                initial={
                    "role": role,
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
        role = form.cleaned_data.get("role")
        status = form.cleaned_data.get("status") or ""
        send_after = form.cleaned_data.get("send_after")

        recipients = get_recipients(event, role=role, status=status)

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
            role_filter=role,
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
                    "Email queued for %(count)d recipient.",
                    "Email queued for %(count)d recipients.",
                    len(recipients),
                )
                % {"count": len(recipients)},
            )
        return redirect(
            "plugins:teamshifts:email_outbox",
            organizer=self.request.organizer.slug,
            event=event.slug,
        )


class EmailOutboxView(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
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
        return ctx


class EmailSentView(PluginActiveMixin, EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
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
        return ctx


class EmailQueueEditView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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
        return render(request, self.template_name, {"form": form, "queue": queue})

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
        return render(request, self.template_name, {"form": form, "queue": queue})


class EmailQueueDeleteView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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


class EmailQueueSendNowView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

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


class ShiftLocationListView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
    template_name = "teamshifts/locations.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            locations = list(ShiftLocation.objects.filter(event=request.event))
        return render(request, self.template_name, {"locations": locations})


class ShiftLocationCreateView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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


class ShiftLocationUpdateView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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


class ShiftLocationDeleteView(PluginActiveMixin, EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
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

import json

from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View
from django_scopes import scope
from eventyay.control.permissions import EventPermissionRequiredMixin

from .forms import (
    CallForTeamMembersForm,
    TeamApplicationQuestionForm,
    TeamMemberApplicationForm,
    TeamRoleForm,
    render_answer_for_review,
)
from .models import (
    ApplicationStatus,
    CallForTeamMembers,
    Shift,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
    TeamRole,
)


class TeamShiftsDashboard(EventPermissionRequiredMixin, TemplateView):
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
            ctx["recent_applications"] = list(TeamMemberApplication.objects.filter(event=event).select_related("user", "role").order_by("-created_at")[:5])
            ctx["accepted_members"] = list(
                TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).select_related("user", "role").order_by("-updated_at")[:8]
            )
            try:
                ctx["cfm"] = event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                ctx["cfm"] = None
        return ctx


class CFMSettingsView(EventPermissionRequiredMixin, View):
    """
    Settings page / form builder: shows CFM settings plus the inline list
    of organiser-defined custom application questions.
    """

    permission = "can_change_event_settings"
    template_name = "teamshifts/cfv_settings.html"

    def _get_cfm(self):
        with scope(event=self.request.event):
            obj, _created = CallForTeamMembers.objects.get_or_create(event=self.request.event)
        return obj

    def _questions(self):
        with scope(event=self.request.event):
            return list(TeamApplicationQuestion.objects.filter(event=self.request.event).select_related("role").order_by("position", "pk"))

    def get(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        form = CallForTeamMembersForm(instance=cfm, locales=request.event.settings.locales)
        return render(request, self.template_name, {"form": form, "cfm": cfm, "questions": self._questions()})

    def post(self, request, *args, **kwargs):
        cfm = self._get_cfm()
        form = CallForTeamMembersForm(request.POST, instance=cfm, locales=request.event.settings.locales)
        if form.is_valid():
            with scope(event=request.event):
                form.save()
            messages.success(request, _("Settings saved."))
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form, "cfm": cfm, "questions": self._questions()})


class TeamRoleListView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"
    template_name = "teamshifts/roles.html"

    def get(self, request, *args, **kwargs):
        with scope(event=request.event):
            roles = list(TeamRole.objects.filter(event=request.event).annotate(application_count=Count("applications")))
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
            roles = list(TeamRole.objects.filter(event=request.event).annotate(application_count=Count("applications")))
        return render(request, self.template_name, {"roles": roles, "form": form})


class TeamRoleDeleteView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            role = get_object_or_404(TeamRole, pk=kwargs["pk"], event=event)
            if role.applications.exists():
                messages.error(request, _("Cannot delete '%s': it has existing applications.") % role.name)
            elif role.shifts.exists():
                messages.error(request, _("Cannot delete '%s': it is used by existing shifts.") % role.name)
            else:
                name = role.name
                role.delete()
                messages.success(request, _("Role '%s' deleted.") % name)
        return redirect("plugins:teamshifts:roles", organizer=request.organizer.slug, event=event.slug)


class QuestionEditView(EventPermissionRequiredMixin, View):
    """Create (no pk) or edit (pk) a custom application question."""

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
                messages.success(request, _("Question '%s' added.") % saved.question)
            else:
                messages.success(request, _("Question saved."))
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form, "question": instance})


class QuestionDeleteView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        event = request.event
        with scope(event=event):
            question = get_object_or_404(TeamApplicationQuestion, pk=kwargs["pk"], event=event)
            label = str(question.question)
            question.delete()
        messages.success(request, _("Question '%s' deleted.") % label)
        return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=event.slug)


class QuestionReorderView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body.decode("utf-8"))
            pks = [str(pk) for pk in data.get("ids", [])]
        except (json.JSONDecodeError, ValueError, AttributeError):
            return HttpResponse(status=400)
        event = request.event
        with scope(event=event):
            for position, pk in enumerate(pks):
                if pk.isdigit():
                    TeamApplicationQuestion.objects.filter(pk=int(pk), event=event).update(position=position)
        return HttpResponse(status=204)


class QuestionToggleView(EventPermissionRequiredMixin, View):
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


class ApplicationListView(EventPermissionRequiredMixin, TemplateView):
    permission = "can_change_event_settings"
    template_name = "teamshifts/applications.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        with scope(event=event):
            qs = TeamMemberApplication.objects.filter(event=event).select_related("user", "role").prefetch_related("answers__question").order_by("-created_at")
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

            applications = list(qs)
            for app in applications:
                app.rendered_answers = [{"question": a.question, "value": render_answer_for_review(a.question, a.answer)} for a in app.answers.all()]

            ctx["applications"] = applications
            ctx["roles"] = list(TeamRole.objects.filter(event=event))
            ctx["status_choices"] = ApplicationStatus.choices
            ctx["current_status"] = status_filter
            ctx["current_role"] = role_filter
            ctx["search"] = search
        return ctx


class ApplicationStatusView(EventPermissionRequiredMixin, View):
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
            elif action == "reject":
                application.status = ApplicationStatus.REJECTED
                application.save(update_fields=["status", "updated_at"])
                messages.warning(request, _("Application by %s rejected.") % application.user.email)
            else:
                raise Http404
        return redirect("plugins:teamshifts:applications", organizer=request.organizer.slug, event=event.slug)


class PublicApplyView(FormView):
    template_name = "teamshifts/apply.html"

    def dispatch(self, request, *args, **kwargs):
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
        with scope(event=self.event):
            kwargs["applied_role_ids"] = list(TeamMemberApplication.objects.filter(event=self.event, user=self.request.user).values_list("role_id", flat=True))
        return TeamMemberApplicationForm(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        ctx["cfm"] = self.cfm
        ctx["cfm_open"] = self.cfm is not None and self.cfm.is_open
        ctx["cfm_deadline_passed"] = self.cfm is not None and self.cfm.active and self.cfm.deadline is not None and not self.cfm.is_open
        with scope(event=self.event):
            ctx["existing_applications"] = list(TeamMemberApplication.objects.filter(event=self.event, user=self.request.user).select_related("role"))
        return ctx

    def form_valid(self, form):
        event = self.event
        if self.cfm is None or not self.cfm.is_open:
            messages.error(self.request, _("Applications are not currently open for this event."))
            return self.form_invalid(form)
        role = form.cleaned_data["role"]
        name = form.cleaned_data.get("name", "").strip()
        with scope(event=event):
            if TeamMemberApplication.objects.filter(event=event, user=self.request.user, role=role).exists():
                messages.error(self.request, _("You have already applied for the role '%s'.") % role.name)
                return self.form_invalid(form)
            try:
                application = TeamMemberApplication.objects.create(
                    event=event,
                    user=self.request.user,
                    role=role,
                    availability_notes=form.cleaned_data.get("availability_notes", ""),
                    phone=form.cleaned_data.get("phone", ""),
                )
            except IntegrityError:
                messages.error(self.request, _("You have already applied for the role '%s'.") % role.name)
                return self.form_invalid(form)
            for question, answer_text in form.get_question_answers():
                if question.role_id and question.role_id != role.pk:
                    continue
                TeamApplicationAnswer.objects.create(application=application, question=question, answer=answer_text)
        if name and name != self.request.user.fullname:
            self.request.user.fullname = name
            self.request.user.save(update_fields=["fullname"])
        messages.success(self.request, _("Your application for '%s' has been submitted.") % role.name)
        return redirect(
            reverse(
                "plugins:teamshifts:apply_thanks",
                kwargs={"organizer": self.organizer.slug, "event": event.slug},
            )
        )


class PublicApplyThanksView(TemplateView):
    template_name = "teamshifts/apply_thanks.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse("eventyay_common:auth.login")
            return redirect(f"{login_url}?next={request.get_full_path()}")
        self.event = request.event
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        return ctx

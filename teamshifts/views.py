from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
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
            ctx["application_count"] = TeamMemberApplication.objects.filter(event=event).count()
            ctx["pending_count"] = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.PENDING).count()
            ctx["accepted_count"] = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).count()
            ctx["recent_applications"] = list(TeamMemberApplication.objects.filter(event=event).select_related("user", "role").order_by("-created_at")[:5])
            try:
                ctx["cfm"] = event.call_for_team_members
            except CallForTeamMembers.DoesNotExist:
                ctx["cfm"] = None
        return ctx


class CFMSettingsView(EventPermissionRequiredMixin, View):
    """
    Settings page that doubles as the form builder (mirrors the orga:cfp.text
    pattern in the talks component): shows CFM settings plus the inline list
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
            form.save()
            messages.success(request, _("Settings saved."))
            return redirect("plugins:teamshifts:cfm_settings", organizer=request.organizer.slug, event=request.event.slug)
        return render(request, self.template_name, {"form": form, "cfm": cfm, "questions": self._questions()})


class TeamRoleListView(EventPermissionRequiredMixin, View):
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
            role.save()
            messages.success(request, _("Role '%s' created.") % role.name)
            return redirect("plugins:teamshifts:roles", organizer=request.organizer.slug, event=request.event.slug)
        with scope(event=request.event):
            roles = list(TeamRole.objects.filter(event=request.event))
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
    """
    Handles both create (no pk / question_create URL) and edit (pk / question_edit URL).
    On success redirects back to cfm_settings where the list lives.
    """

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
            if role_filter:
                qs = qs.filter(role__pk=role_filter)
            if search:
                qs = qs.filter(user__email__icontains=search)

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
            application = get_object_or_404(TeamMemberApplication, pk=kwargs["pk"], event=event)
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
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
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
        return TeamMemberApplicationForm(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        ctx["cfm"] = self.cfm
        ctx["cfm_open"] = self.cfm is not None and self.cfm.active
        with scope(event=self.event):
            ctx["existing_applications"] = list(TeamMemberApplication.objects.filter(event=self.event, user=self.request.user).select_related("role"))
        return ctx

    def form_valid(self, form):
        event = self.event
        if self.cfm is None or not self.cfm.active:
            messages.error(self.request, _("Applications are not currently open for this event."))
            return self.form_invalid(form)
        role = form.cleaned_data["role"]
        with scope(event=event):
            if TeamMemberApplication.objects.filter(event=event, user=self.request.user, role=role).exists():
                messages.error(self.request, _("You have already applied for the role '%s'.") % role.name)
                return self.form_invalid(form)
            application = TeamMemberApplication.objects.create(
                event=event,
                user=self.request.user,
                role=role,
                availability_notes=form.cleaned_data.get("availability_notes", ""),
            )
            for question, answer_text in form.get_question_answers():
                if question.role_id and question.role_id != role.pk:
                    continue
                TeamApplicationAnswer.objects.create(application=application, question=question, answer=answer_text)
        messages.success(self.request, _("Your application for '%s' has been submitted.") % role.name)
        return redirect("plugins:teamshifts:apply_thanks", organizer=self.organizer.slug, event=event.slug)


class PublicApplyThanksView(TemplateView):
    template_name = "teamshifts/apply_thanks.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        self.event = request.event
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.event
        return ctx

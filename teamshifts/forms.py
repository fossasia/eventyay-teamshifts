from django import forms
from django.utils.translation import gettext_lazy as _
from django_scopes import scopes_disabled

from .models import (
    CallForTeamMembers,
    QuestionVariant,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
    TeamRole,
)


class CallForTeamMembersForm(forms.ModelForm):
    class Meta:
        model = CallForTeamMembers
        fields = ("title", "active", "deadline", "description")
        widgets = {
            "deadline": forms.DateTimeInput(
                attrs={"class": "form-control datetimepicker"},
                format="%Y-%m-%d %H:%M:%S",
            ),
            "title": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, locales=None, **kwargs):
        super().__init__(*args, **kwargs)
        if locales:
            self.fields["description"].widget.enabled_locales = locales


class TeamRoleForm(forms.ModelForm):
    class Meta:
        model = TeamRole
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class TeamApplicationQuestionForm(forms.ModelForm):
    # Declare role explicitly to avoid hitting the scoped manager at class-definition time.
    role = forms.ModelChoiceField(
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text=_("Pick a role to show this question for that role only, or leave blank for every role."),
    )

    class Meta:
        model = TeamApplicationQuestion
        fields = ("role", "question", "help_text", "variant", "required", "options", "active", "position")
        widgets = {
            "options": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": _("One option per line")},
            ),
            "position": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "variant": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, event=None, locales=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        if event is not None:
            with scopes_disabled():
                self.fields["role"].queryset = TeamRole.objects.filter(event=event)
        if locales:
            for field_name in ("question", "help_text"):
                self.fields[field_name].widget.enabled_locales = locales

    def clean(self):
        cleaned = super().clean()
        variant = cleaned.get("variant")
        options = cleaned.get("options", "")
        needs_options = variant in (QuestionVariant.CHOICES, QuestionVariant.CHOICES_DROPDOWN, QuestionVariant.MULTIPLE)
        if needs_options and len([l for l in (options or "").splitlines() if l.strip()]) < 2:
            self.add_error("options", _("Choice fields need at least two options, one per line."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self._event is not None:
            instance.event = self._event
        if commit:
            instance.save()
        return instance


class TeamMemberApplicationForm(forms.Form):
    role = forms.ModelChoiceField(
        queryset=TeamRole.objects.none(),
        label=_("Role you are applying for"),
        empty_label=_("— Select a role —"),
        widget=forms.Select(attrs={"class": "form-control", "id": "id_role"}),
    )
    availability_notes = forms.CharField(
        required=False,
        label=_("Availability notes"),
        help_text=_("Which days/hours can you commit to?"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    phone = forms.CharField(
        required=False,
        label=_("Phone / Mobile"),
        help_text=_("Optional. We may use this to contact you regarding your shift."),
        widget=forms.TextInput(attrs={"class": "form-control", "type": "tel", "placeholder": "+1 555 000 0000"}),
    )

    QUESTION_FIELD_PREFIX = "question_"

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        self._questions = []
        if event is None:
            return
        with scopes_disabled():
            self.fields["role"].queryset = TeamRole.objects.filter(event=event)
            self._questions = list(TeamApplicationQuestion.objects.filter(event=event, active=True).order_by("position", "pk"))
        for question in self._questions:
            field = self._build_field_for_question(question)
            # Store which role (if any) this question belongs to so the
            # template JS can show/hide it when the role selection changes.
            role_pk = str(question.role_id) if question.role_id else ""
            field.widget.attrs["data-question-role"] = role_pk
            field.widget.attrs["data-question-field"] = "1"
            self.fields[self._field_name_for(question)] = field

    @staticmethod
    def _field_name_for(question):
        return f"{TeamMemberApplicationForm.QUESTION_FIELD_PREFIX}{question.pk}"

    @staticmethod
    def _build_field_for_question(question):
        label = str(question.question)
        help_text = str(question.help_text) if question.help_text else ""
        required = bool(question.required)
        variant = question.variant
        common = {"label": label, "help_text": help_text, "required": required}

        if variant == QuestionVariant.STRING:
            return forms.CharField(widget=forms.TextInput(attrs={"class": "form-control"}), **common)
        if variant == QuestionVariant.TEXT:
            return forms.CharField(widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}), **common)
        if variant == QuestionVariant.NUMBER:
            return forms.DecimalField(widget=forms.NumberInput(attrs={"class": "form-control"}), **common)
        if variant == QuestionVariant.URL:
            return forms.URLField(widget=forms.URLInput(attrs={"class": "form-control"}), **common)
        if variant == QuestionVariant.DATE:
            return forms.DateField(widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}), **common)
        if variant == QuestionVariant.DATETIME:
            return forms.DateTimeField(widget=forms.DateTimeInput(attrs={"class": "form-control datetimepicker"}), **common)
        if variant == QuestionVariant.PHONE:
            return forms.CharField(widget=forms.TextInput(attrs={"class": "form-control", "type": "tel"}), **common)
        if variant == QuestionVariant.COUNTRY:
            from django_countries import countries

            return forms.ChoiceField(
                choices=[("", _("— Select country —"))] + list(countries),
                widget=forms.Select(attrs={"class": "form-control"}),
                **common,
            )
        if variant == QuestionVariant.BOOLEAN:
            if required:
                return forms.BooleanField(**common)
            return forms.NullBooleanField(
                widget=forms.Select(
                    attrs={"class": "form-control"},
                    choices=[("unknown", _("—")), ("true", _("Yes")), ("false", _("No"))],
                ),
                **common,
            )
        options = [(opt, opt) for opt in question.get_options()]
        if variant == QuestionVariant.CHOICES:
            return forms.ChoiceField(
                choices=([("", _("— Select —"))] if not required else []) + options,
                widget=forms.RadioSelect,
                **common,
            )
        if variant == QuestionVariant.CHOICES_DROPDOWN:
            return forms.ChoiceField(
                choices=([("", _("— Select —"))] if not required else []) + options,
                widget=forms.Select(attrs={"class": "form-control"}),
                **common,
            )
        if variant == QuestionVariant.MULTIPLE:
            return forms.MultipleChoiceField(choices=options, widget=forms.CheckboxSelectMultiple, **common)
        return forms.CharField(widget=forms.TextInput(attrs={"class": "form-control"}), **common)

    def visible_questions(self):
        return [(q, self[self._field_name_for(q)]) for q in self._questions]

    def get_question_answers(self):
        return [(q, self._serialize_answer(q, self.cleaned_data.get(self._field_name_for(q)))) for q in self._questions]

    @staticmethod
    def _serialize_answer(question, value):
        if value is None or value == "":
            return ""
        variant = question.variant
        if variant == QuestionVariant.BOOLEAN:
            return "true" if value is True else ("false" if value is False else "")
        if variant == QuestionVariant.MULTIPLE:
            return "\n".join(value) if isinstance(value, (list, tuple)) else str(value)
        if variant == QuestionVariant.DATE:
            return f"{value:%Y-%m-%d}"
        if variant == QuestionVariant.DATETIME:
            return f"{value:%Y-%m-%d %H:%M}"
        return str(value)


def render_answer_for_review(question, answer_text):
    if not answer_text:
        return ""
    if question.variant == QuestionVariant.BOOLEAN:
        return _("Yes") if answer_text == "true" else _("No")
    if question.variant == QuestionVariant.MULTIPLE:
        return ", ".join(line for line in answer_text.splitlines() if line)
    return answer_text


__all__ = [
    "CallForTeamMembersForm",
    "TeamRoleForm",
    "TeamApplicationQuestionForm",
    "TeamMemberApplicationForm",
    "TeamApplicationAnswer",
    "render_answer_for_review",
]

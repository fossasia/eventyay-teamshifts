from django import forms
from django.utils.translation import gettext_lazy as _
from django_countries import countries
from django_scopes import scopes_disabled
from i18nfield.forms import I18nFormField, I18nTextarea, I18nTextInput

from .models import (
    CFM_BUILTIN_FIELD_KEYS,
    ApplicationStatus,
    AskChoices,
    CallForTeamMembers,
    QuestionVariant,
    TeamApplicationQuestion,
    TeamRole,
    TeamShiftsEmailQueue,
    normalize_field_order,
)


class CallForTeamMembersSettingsForm(forms.ModelForm):
    class Meta:
        model = CallForTeamMembers
        fields = (
            "title",
            "active",
            "show_on_menu",
            "deadline",
            "description",
        )
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


class CallForTeamMembersApplicationSettingsForm(forms.ModelForm):
    class Meta:
        model = CallForTeamMembers
        fields = (
            "ask_full_name",
            "ask_phone",
            "ask_availability",
        )


class TeamRoleForm(forms.ModelForm):
    class Meta:
        model = TeamRole
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class TeamApplicationQuestionForm(forms.ModelForm):
    # Declared explicitly to avoid the scoped manager firing at class-definition time.
    role = forms.ModelChoiceField(
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text=_("Pick a role to show this question for that role only, or leave blank for every role."),
    )

    class Meta:
        model = TeamApplicationQuestion
        fields = ("role", "question", "help_text", "variant", "required", "options", "active")
        widgets = {
            "options": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": _("One option per line")},
            ),
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
        if needs_options and len([line for line in (options or "").splitlines() if line.strip()]) < 2:
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
    QUESTION_FIELD_PREFIX = "question_"

    def __init__(self, *args, event=None, user=None, applied_role_ids=(), cfm=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        self._questions: list[TeamApplicationQuestion] = []
        self._field_order_keys: list = []

        if user is not None:
            self._user = user
        else:
            self._user = None

        if event is None:
            return

        with scopes_disabled():
            role_qs = TeamRole.objects.filter(event=event).exclude(pk__in=applied_role_ids)
            self._questions = list(TeamApplicationQuestion.objects.filter(event=event, active=True).order_by("pk"))

        question_map: dict[int, TeamApplicationQuestion] = {q.pk: q for q in self._questions}

        if cfm is not None:
            raw_order = normalize_field_order(list(cfm.field_order))
        else:
            raw_order = list(CFM_BUILTIN_FIELD_KEYS)

        raw_order = [int(i) if isinstance(i, str) and i.isdigit() else i for i in raw_order]
        present_question_pks = {i for i in raw_order if isinstance(i, int)}
        for q in self._questions:
            if q.pk not in present_question_pks:
                raw_order.append(q.pk)

        for item in raw_order:
            if isinstance(item, str):
                ask_state = cfm.get_ask_state(item) if cfm else AskChoices.OPTIONAL
                if ask_state == AskChoices.DO_NOT_ASK:
                    continue
                required = ask_state == AskChoices.REQUIRED
                self._field_order_keys.append(item)

                if item == "role":
                    field = forms.ModelChoiceField(
                        queryset=role_qs,
                        label=_("Role you are applying for"),
                        required=True,
                        empty_label=_("— Select a role —"),
                        widget=forms.Select(attrs={"class": "form-control", "id": "id_role"}),
                    )
                    self.fields["role"] = field
                elif item == "full_name":
                    field = forms.CharField(
                        label=_("Full name"),
                        required=required,
                        widget=forms.TextInput(attrs={"class": "form-control"}),
                    )
                    if user is not None:
                        field.initial = user.fullname or ""
                    self.fields["full_name"] = field
                elif item == "email":
                    field = forms.EmailField(
                        label=_("Email address"),
                        required=True,
                        widget=forms.EmailInput(attrs={"class": "form-control", "readonly": True}),
                        help_text=_("To change your email address, visit your account settings."),
                    )
                    if user is not None:
                        field.initial = user.email
                    self.fields["email"] = field
                elif item == "phone":
                    field = forms.CharField(
                        label=_("Phone / Mobile"),
                        required=required,
                        help_text=_("Optional. We may use this to contact you regarding your shift."),
                        widget=forms.TextInput(attrs={"class": "form-control", "type": "tel", "placeholder": "+1 555 000 0000"}),
                    )
                    self.fields["phone"] = field
                elif item == "availability":
                    field = forms.CharField(
                        label=_("Availability notes"),
                        required=required,
                        help_text=_("Which days/hours can you commit to?"),
                        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
                    )
                    self.fields["availability_notes"] = field
            else:
                pk = int(item)
                question = question_map.get(pk)
                if question is None:
                    continue
                self._field_order_keys.append(item)
                field = self._build_field_for_question(question)
                role_pk = str(question.role_id) if question.role_id else ""
                field.widget.attrs["data-question-role"] = role_pk
                field.widget.attrs["data_question_role"] = role_pk
                field.widget.attrs["data-question-field"] = "1"
                field.widget.attrs["data_question_field"] = "1"
                if question.role_id:
                    field.required = False
                self.fields[self._field_name_for(question)] = field

    @staticmethod
    def _field_name_for(question: TeamApplicationQuestion) -> str:
        return f"{TeamMemberApplicationForm.QUESTION_FIELD_PREFIX}{question.pk}"

    @staticmethod
    def _build_field_for_question(question: TeamApplicationQuestion) -> forms.Field:
        label = str(question.question)
        help_text = str(question.help_text) if question.help_text else ""
        required = bool(question.required)
        variant = question.variant
        common: dict = {"label": label, "help_text": help_text, "required": required}

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
            return forms.ChoiceField(
                choices=[("", _("— Select country —"))] + list(countries),
                widget=forms.Select(attrs={"class": "form-control"}),
                **common,
            )
        if variant == QuestionVariant.BOOLEAN:
            if required:
                return forms.BooleanField(**common)
            return forms.TypedChoiceField(
                choices=[("", _("—")), ("true", _("Yes")), ("false", _("No"))],
                coerce=lambda v: True if v == "true" else (False if v == "false" else None),
                empty_value=None,
                widget=forms.Select(attrs={"class": "form-control"}),
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

    def visible_questions(self) -> list[tuple[TeamApplicationQuestion, forms.BoundField]]:
        return [(q, self[self._field_name_for(q)]) for q in self._questions]

    def render_items(self):
        """Yield {field, is_question, role_id} for the template so it can wrap
        role-scoped custom question fields without needing dashed-attr lookup."""
        question_map = {q.pk: q for q in self._questions}
        for name in self.fields:
            if name.startswith(self.QUESTION_FIELD_PREFIX):
                try:
                    pk = int(name[len(self.QUESTION_FIELD_PREFIX) :])
                except ValueError:
                    continue
                question = question_map.get(pk)
                role_id = str(question.role_id) if question and question.role_id else ""
                yield {"field": self[name], "is_question": True, "role_id": role_id}
            else:
                yield {"field": self[name], "is_question": False, "role_id": ""}

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role is None:
            return cleaned
        for question in self._questions:
            if question.role_id and question.role_id != role.pk:
                continue
            if not question.required:
                continue
            value = cleaned.get(self._field_name_for(question))
            if question.variant == QuestionVariant.BOOLEAN:
                if value is not True:
                    self.add_error(self._field_name_for(question), _("This field is required."))
            elif value in (None, "", [], (), {}):
                self.add_error(self._field_name_for(question), _("This field is required."))
        return cleaned

    def get_question_answers(self) -> list[tuple[TeamApplicationQuestion, str]]:
        return [(q, self._serialize_answer(q, self.cleaned_data.get(self._field_name_for(q)))) for q in self._questions]

    @staticmethod
    def _serialize_answer(question: TeamApplicationQuestion, value) -> str:
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


def render_answer_for_review(question: TeamApplicationQuestion, answer_text: str) -> str:
    if not answer_text:
        return ""
    if question.variant == QuestionVariant.BOOLEAN:
        return _("Yes") if answer_text == "true" else _("No")
    if question.variant == QuestionVariant.MULTIPLE:
        return ", ".join(line for line in answer_text.splitlines() if line)
    return answer_text


class EmailTemplateForm(forms.ModelForm):
    class Meta:
        from .models import TeamShiftsEmailTemplate

        model = TeamShiftsEmailTemplate
        fields = ("subject", "body")

    def __init__(self, *args, locales=None, **kwargs):
        super().__init__(*args, **kwargs)
        if locales:
            for field_name in ("subject", "body"):
                self.fields[field_name].widget.enabled_locales = locales


class EmailComposeForm(forms.Form):
    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        locales = list(event.settings.get("locales") or [event.settings.locale])

        self.fields["subject"] = I18nFormField(
            label=_("Subject"),
            widget=I18nTextInput,
            required=True,
            locales=locales,
        )
        self.fields["message"] = I18nFormField(
            label=_("Message"),
            widget=I18nTextarea,
            required=True,
            locales=locales,
            widget_kwargs={"attrs": {"rows": 10}},
        )

        with scopes_disabled():
            role_qs = TeamRole.objects.filter(event=event)
        self.fields["role"] = forms.ModelChoiceField(
            queryset=role_qs,
            required=False,
            empty_label=_("All roles"),
            label=_("Send to role"),
            widget=forms.Select(attrs={"class": "form-control"}),
        )

        self.fields["status"] = forms.ChoiceField(
            choices=[("", _("All statuses"))] + list(ApplicationStatus.choices),
            required=False,
            initial=ApplicationStatus.ACCEPTED,
            label=_("Send to applications with status"),
            widget=forms.Select(attrs={"class": "form-control"}),
        )

        self.fields["send_after"] = forms.DateTimeField(
            required=False,
            label=_("Schedule for later"),
            help_text=_("Leave empty to send immediately. Otherwise the message stays in the outbox until the scheduled time."),
            widget=forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        )


class EmailQueueEditForm(forms.ModelForm):
    class Meta:
        model = TeamShiftsEmailQueue
        fields = ("subject", "message", "send_after")
        widgets = {
            "send_after": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        if event is not None:
            locales = list(event.settings.get("locales") or [event.settings.locale])
            for field_name in ("subject", "message"):
                self.fields[field_name].widget.enabled_locales = locales
        self.fields["send_after"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]


__all__ = [
    "CallForTeamMembersSettingsForm",
    "CallForTeamMembersApplicationSettingsForm",
    "TeamRoleForm",
    "TeamApplicationQuestionForm",
    "TeamMemberApplicationForm",
    "EmailTemplateForm",
    "EmailComposeForm",
    "EmailQueueEditForm",
    "render_answer_for_review",
    "ShiftLocationForm",
]


class ShiftLocationForm(forms.ModelForm):
    class Meta:
        from .models import ShiftLocation

        model = ShiftLocation
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

import re
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone
from django.utils.html import escape as html_escape
from django.utils.translation import gettext_lazy as _
from django_countries import countries
from django_scopes import scopes_disabled
from django_scopes.forms import SafeModelChoiceField
from eventyay.common.forms.widgets import I18nEmailEditorWidget, RichTextWidget
from eventyay.control.forms import SplitDateTimeField, SplitDateTimePickerWidget
from i18nfield.forms import I18nFormField, I18nTextInput

from .models import (
    CFM_BUILTIN_FIELD_KEYS,
    ApplicationStatus,
    AskChoices,
    CallForTeamMembers,
    CertificateMatchMode,
    CertificateSettings,
    CertificateTrigger,
    QuestionVariant,
    ShiftLocation,
    TeamApplicationQuestion,
    TeamRole,
    TeamShiftsEmailQueue,
    normalize_field_order,
)


def get_tz_help(event):
    return _("Times are in the event timezone: %(tz)s.") % {"tz": event.timezone}


def get_event_local_now(event):
    return timezone.localtime(timezone.now(), ZoneInfo(event.timezone))


def format_datetime_local(dt):
    return f"{dt:%Y-%m-%dT%H:%M}"


EMAIL_PLACEHOLDERS = ["full_name", "event_name", "role_name", "event_dates", "event_location", "shift_schedule_url"]

_BLOCK_TAG_RE = re.compile(r"^\s*<(p|ul|ol|blockquote|div|h[1-6])[\s>]", re.IGNORECASE)


def plain_text_to_html(text: str) -> str:
    if not text:
        return text
    if "data-variable=" in text or _BLOCK_TAG_RE.match(text):
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n{2,}", text)
    parts = []
    for para in paragraphs:
        stripped = para.strip()
        if stripped:
            inner = html_escape(stripped).replace("\n", "<br>")
            parts.append(f"<p>{inner}</p>")
    return "".join(parts) if parts else text


class _HtmlNormalizingEmailWidget(I18nEmailEditorWidget):
    def decompress(self, value):
        values = super().decompress(value)
        return [plain_text_to_html(v) if v else v for v in values]


class CallForTeamMembersSettingsForm(forms.ModelForm):
    class Meta:
        model = CallForTeamMembers
        fields = (
            "title",
            "active",
            "show_on_menu",
            "cfm_private",
            "deadline",
            "description",
        )
        field_classes = {
            "deadline": SplitDateTimeField,
        }
        widgets = {
            "deadline": SplitDateTimePickerWidget(),
            "title": forms.TextInput(attrs={"class": "form-control"}),
        }
        help_texts = {}

    def __init__(self, *args, locales=None, **kwargs):
        self._event = kwargs.pop("event", None)
        super().__init__(*args, **kwargs)
        if locales:
            self.fields["description"].widget = I18nEmailEditorWidget(
                locales=locales,
                field=self.fields["description"],
                attrs={"data-tiptap-profile": "richtext"},
            )
            self.fields["description"].widget.enabled_locales = locales
        if self._event:
            self.fields["deadline"].help_text = get_tz_help(self._event)
            if not self.initial.get("deadline"):
                self.initial["deadline"] = get_event_local_now(self._event)


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
        fields = ("name", "description", "is_restricted")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": RichTextWidget(attrs={"class": "form-control", "rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        if name and self.instance and hasattr(self.instance, "event_id") and self.instance.event_id:
            qs = TeamRole.objects.filter(event_id=self.instance.event_id, name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("name", _("A role with this name already exists for this event."))
        return cleaned_data


class TeamApplicationQuestionForm(forms.ModelForm):
    class Meta:
        model = TeamApplicationQuestion
        fields = ("question", "help_text", "variant", "required", "options", "active")
        widgets = {
            "options": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": _("One option per line")},
            ),
            "variant": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, event=None, locales=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
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

    def __init__(self, *args, event=None, user=None, cfm=None, organizer_mode=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        self._questions: list[TeamApplicationQuestion] = []
        self._field_order_keys: list = []
        self._organizer_mode = organizer_mode

        if user is not None:
            self._user = user
        else:
            self._user = None

        if event is None:
            return

        with scopes_disabled():
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

        # Organizers must always be able to enter email (and usually a name) when adding a member.
        if organizer_mode:
            if "email" not in raw_order:
                raw_order.insert(0, "email")
            if "full_name" not in {i for i in raw_order if isinstance(i, str)}:
                email_idx = raw_order.index("email") if "email" in raw_order else 0
                raw_order.insert(email_idx, "full_name")

        for item in raw_order:
            if isinstance(item, str):
                ask_state = cfm.get_ask_state(item) if cfm else AskChoices.OPTIONAL
                if item == "email" and organizer_mode:
                    ask_state = AskChoices.REQUIRED
                elif item == "full_name" and organizer_mode and ask_state == AskChoices.DO_NOT_ASK:
                    ask_state = AskChoices.OPTIONAL
                if ask_state == AskChoices.DO_NOT_ASK:
                    continue
                required = ask_state == AskChoices.REQUIRED
                self._field_order_keys.append(item)

                if item == "full_name":
                    field = forms.CharField(
                        label=_("Full name"),
                        required=required,
                        widget=forms.TextInput(attrs={"class": "form-control"}),
                    )
                    if user is not None:
                        field.initial = user.fullname or ""
                    self.fields["full_name"] = field
                elif item == "email":
                    if organizer_mode:
                        field = forms.EmailField(
                            label=_("Email address"),
                            required=True,
                            widget=forms.EmailInput(attrs={"class": "form-control"}),
                            help_text=_("If no account exists for this email, one will be created."),
                        )
                    else:
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
                field.widget.attrs["data-question-field"] = "1"
                field.widget.attrs["data_question_field"] = "1"
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
        for name in self.fields:
            if name.startswith(self.QUESTION_FIELD_PREFIX):
                try:
                    int(name[len(self.QUESTION_FIELD_PREFIX) :])
                except ValueError:
                    continue
                yield {"field": self[name], "is_question": True, "role_id": ""}
            else:
                yield {"field": self[name], "is_question": False, "role_id": ""}

    def clean(self):
        cleaned = super().clean()
        for question in self._questions:
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
        labels = {
            "subject": _("Subject"),
            "body": _("Body"),
        }

    def __init__(self, *args, locales=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].required = False
        self.fields["body"].required = False
        if locales:
            self.fields["subject"].widget = I18nTextInput(locales=locales, field=self.fields["subject"])
            self.fields["body"].widget = _HtmlNormalizingEmailWidget(
                locales=locales,
                field=self.fields["body"],
                placeholders=EMAIL_PLACEHOLDERS,
            )
            for field_name in ("subject", "body"):
                self.fields[field_name].widget.enabled_locales = locales


class CustomEmailTemplateForm(forms.ModelForm):
    class Meta:
        from .models import TeamShiftsCustomEmailTemplate

        model = TeamShiftsCustomEmailTemplate
        fields = ("name", "subject", "body")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, locales=None, **kwargs):
        super().__init__(*args, **kwargs)
        if locales:
            self.fields["subject"].widget = I18nTextInput(locales=locales, field=self.fields["subject"])
            self.fields["body"].widget = _HtmlNormalizingEmailWidget(
                locales=locales,
                field=self.fields["body"],
                placeholders=EMAIL_PLACEHOLDERS,
            )
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
            widget=_HtmlNormalizingEmailWidget,
            required=True,
            locales=locales,
            widget_kwargs={
                "attrs": {"rows": 10},
                "placeholders": EMAIL_PLACEHOLDERS,
            },
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
            help_text=_("Leave empty to send immediately. Otherwise the message stays in the outbox until the scheduled time.")
            + (f" {get_tz_help(self._event)}" if self._event else ""),
            widget=forms.DateTimeInput(
                attrs={
                    "class": "form-control",
                    "type": "datetime-local",
                    **({"data-schedule-datetime": "1", "data-event-timezone": self._event.timezone} if self._event else {}),
                },
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
        help_texts = {}

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._event = event
        if event is not None:
            locales = list(event.settings.get("locales") or [event.settings.locale])
            self.fields["message"].widget = _HtmlNormalizingEmailWidget(
                locales=locales,
                field=self.fields["message"],
                placeholders=EMAIL_PLACEHOLDERS,
            )
            for field_name in ("subject", "message"):
                self.fields[field_name].widget.enabled_locales = locales
            self.fields["send_after"].help_text = get_tz_help(event)
            self.fields["send_after"].widget.attrs.update(
                {
                    "data-schedule-datetime": "1",
                    "data-event-timezone": event.timezone,
                }
            )
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
    "ShiftForm",
    "ShiftRoleAssignmentForm",
    "BaseShiftRoleFormSet",
    "CertificateSettingsForm",
]


class ShiftLocationForm(forms.ModelForm):
    class Meta:
        model = ShiftLocation
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": RichTextWidget(attrs={"class": "form-control", "rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        if name and self.instance and hasattr(self.instance, "event_id") and self.instance.event_id:
            qs = ShiftLocation.objects.filter(event_id=self.instance.event_id, name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("name", _("A location with this name already exists for this event."))
        return cleaned_data


class ShiftForm(forms.ModelForm):
    mode = forms.ChoiceField(
        choices=[("single", _("Single shift")), ("repeating", _("Repeating shifts"))],
        initial="single",
        widget=forms.RadioSelect,
    )
    shift_length_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        label=_("Length (minutes)"),
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )

    class Meta:
        from .models import Shift

        model = Shift
        fields = ("name", "location", "start_time", "end_time", "description")
        field_classes = {
            "location": SafeModelChoiceField,
        }
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}, format="%Y-%m-%dT%H:%M"),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}, format="%Y-%m-%dT%H:%M"),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "location": forms.Select(attrs={"class": "form-control"}),
            "description": RichTextWidget(attrs={"class": "form-control", "rows": 3}),
        }
        help_texts = {}

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].required = True
        self.fields["start_time"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
        self.fields["end_time"].input_formats = self.fields["start_time"].input_formats
        if event is not None:
            self.fields["start_time"].help_text = get_tz_help(event)
            self.fields["end_time"].help_text = get_tz_help(event)
            from django_scopes import scopes_disabled

            from .models import ShiftLocation

            if not self.instance.pk:
                now_local = format_datetime_local(get_event_local_now(event))
                if not self.initial.get("start_time"):
                    self.initial["start_time"] = now_local
                if not self.initial.get("end_time"):
                    self.initial["end_time"] = now_local

            with scopes_disabled():
                self.fields["location"].queryset = ShiftLocation.objects.filter(event=event)

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if start_time and end_time and end_time <= start_time:
            self.add_error("end_time", _("End time must be after the start time."))

        mode = cleaned_data.get("mode")
        if mode == "repeating":
            shift_length = cleaned_data.get("shift_length_minutes")

            if not shift_length:
                self.add_error("shift_length_minutes", _("Please provide a shift length."))
            elif start_time and end_time and end_time > start_time:
                duration_seconds = int((end_time - start_time).total_seconds())
                if duration_seconds % (shift_length * 60) != 0:
                    self.add_error("shift_length_minutes", _("The shift length must divide evenly into the total duration between start and end time."))
                else:
                    count = duration_seconds // (shift_length * 60)
                    if count > 50:
                        self.add_error(
                            "shift_length_minutes",
                            _("The maximum allowed is 50 per action. Please adjust the interval or date range."),
                        )
        return cleaned_data


class BaseShiftRoleFormSet(forms.BaseInlineFormSet):
    def validate_unique(self):
        pass

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        roles = set()
        has_role = False
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue
            role = form.cleaned_data.get("role")
            if role:
                has_role = True
                if role in roles:
                    raise forms.ValidationError(_("The same role cannot be added twice on the same shift."))
                roles.add(role)

        if not has_role:
            raise forms.ValidationError(_("At least one role must be added to the shift."))


class ShiftRoleAssignmentForm(forms.ModelForm):
    capacity = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
        label=_("Capacity"),
    )

    class Meta:
        from .models import ShiftRoleAssignment

        model = ShiftRoleAssignment
        fields = ("role", "capacity")
        field_classes = {
            "role": SafeModelChoiceField,
        }
        widgets = {
            "role": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["capacity"].min_value = 1
        if event is not None:
            from django_scopes import scopes_disabled

            from .models import TeamRole

            with scopes_disabled():
                self.fields["role"].queryset = TeamRole.objects.filter(event=event)


class CertificateSettingsForm(forms.ModelForm):
    generate_automatically = forms.BooleanField(
        required=False,
        label=_("Generate automatically"),
        help_text=_(
            "When enabled, a certificate is generated as soon as a member meets the conditions, for example "
            "after they are marked arrived. When disabled, an organizer generates certificates manually."
        ),
    )

    class Meta:
        model = CertificateSettings
        fields = (
            "require_arrived",
            "require_min_shifts",
            "min_shifts",
            "match_mode",
        )
        widgets = {
            "require_arrived": forms.CheckboxInput(),
            "require_min_shifts": forms.CheckboxInput(),
            "min_shifts": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "match_mode": forms.RadioSelect(choices=CertificateMatchMode.choices),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["generate_automatically"].initial = self.instance.trigger == CertificateTrigger.AUTO

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("require_arrived") and not cleaned.get("require_min_shifts"):
            raise forms.ValidationError(_("Select at least one qualification condition."))
        min_shifts = cleaned.get("min_shifts") or 1
        if cleaned.get("require_min_shifts") and min_shifts < 1:
            self.add_error("min_shifts", _("Enter at least 1 completed shift."))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.trigger = CertificateTrigger.AUTO if self.cleaned_data.get("generate_automatically") else CertificateTrigger.MANUAL
        if commit:
            instance.save()
        return instance

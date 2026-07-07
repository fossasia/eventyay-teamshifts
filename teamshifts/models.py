from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_scopes import ScopedManager
from i18nfield.fields import I18nTextField

CFM_BUILTIN_FIELD_KEYS = ("full_name", "email", "phone", "role", "availability")

CFM_LOCKED_FIELDS = frozenset({"email", "role"})


def normalize_field_order(order: list) -> list:
    builtin = list(CFM_BUILTIN_FIELD_KEYS)
    builtin_set = set(builtin)
    missing = [f for f in builtin if f not in order]
    if not missing:
        return order
    result = list(order)
    for field in missing:
        canonical_idx = builtin.index(field)
        insert_after = -1
        for i, item in enumerate(result):
            if item in builtin_set and builtin.index(item) < canonical_idx:
                insert_after = i
        result.insert(insert_after + 1, field)
    return result


class ApplicationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")


class AskChoices(models.TextChoices):
    DO_NOT_ASK = "do_not_ask", _("Do not ask")
    OPTIONAL = "optional", _("Optional")
    REQUIRED = "required", _("Required")


class CallForTeamMembers(models.Model):
    """
    Call for Team Members settings for an event.
    One per event. The organiser can customise the public-facing title
    (e.g. "Call for Volunteers", "Call for Team Members").
    """

    event = models.OneToOneField(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="call_for_team_members",
    )
    active = models.BooleanField(default=False, verbose_name=_("Active"), help_text=_("When enabled, the application form accepts new submissions."))
    show_on_menu = models.BooleanField(
        default=True,
        verbose_name=_("Show in public navigation"),
        help_text=_("Show a link to the application form in the public event navigation. Only visible when Active is enabled."),
    )
    deadline = models.DateTimeField(null=True, blank=True, verbose_name=_("Deadline"))
    title = models.CharField(
        max_length=200,
        default=_("Call for Team Members"),
        verbose_name=_("Title"),
        help_text=_("Displayed to applicants. Can be customised, e.g. 'Call for Volunteers' or 'Call for Staff'."),
    )
    description = I18nTextField(verbose_name=_("Description"), blank=True, null=True)

    ask_full_name = models.CharField(
        max_length=20,
        choices=AskChoices.choices,
        default=AskChoices.OPTIONAL,
        verbose_name=_("Full name"),
    )
    ask_email = models.CharField(
        max_length=20,
        choices=AskChoices.choices,
        default=AskChoices.REQUIRED,
        verbose_name=_("Email address"),
    )
    ask_phone = models.CharField(
        max_length=20,
        choices=AskChoices.choices,
        default=AskChoices.OPTIONAL,
        verbose_name=_("Phone / Mobile"),
    )
    ask_role = models.CharField(
        max_length=20,
        choices=AskChoices.choices,
        default=AskChoices.REQUIRED,
        verbose_name=_("Role"),
    )
    ask_availability = models.CharField(
        max_length=20,
        choices=AskChoices.choices,
        default=AskChoices.OPTIONAL,
        verbose_name=_("Availability notes"),
    )

    field_order = models.JSONField(default=list, verbose_name=_("Field order"))

    objects = ScopedManager(event="event")

    class Meta:
        verbose_name = _("Call for Team Members")
        verbose_name_plural = _("Calls for Team Members")

    @property
    def is_open(self):
        if not self.active:
            return False
        if self.deadline and timezone.now() > self.deadline:
            return False
        return True

    def get_ask_state(self, field_key: str) -> str:
        if field_key in CFM_LOCKED_FIELDS:
            return AskChoices.REQUIRED
        mapping = {
            "full_name": self.ask_full_name,
            "email": self.ask_email,
            "phone": self.ask_phone,
            "role": self.ask_role,
            "availability": self.ask_availability,
        }
        return mapping.get(field_key, AskChoices.DO_NOT_ASK)

    def __str__(self):
        return f"{self.title} — {self.event.slug} ({'active' if self.active else 'inactive'})"


class TeamRole(models.Model):
    event = models.ForeignKey(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="team_roles",
    )
    name = models.CharField(max_length=190, verbose_name=_("Role Name"))
    description = models.TextField(blank=True, verbose_name=_("Description"))

    objects = ScopedManager(event="event")

    class Meta:
        verbose_name = _("Team Role")
        verbose_name_plural = _("Team Roles")
        unique_together = ("event", "name")

    def __str__(self):
        return f"{self.name} ({self.event.slug})"


class TeamMemberApplication(models.Model):
    event = models.ForeignKey(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="team_member_applications",
    )
    user = models.ForeignKey(
        "base.User",
        on_delete=models.CASCADE,
        related_name="team_member_applications",
    )
    role = models.ForeignKey(
        TeamRole,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.PENDING,
        verbose_name=_("Status"),
    )
    availability_notes = models.TextField(
        blank=True,
        verbose_name=_("Availability Notes"),
        help_text=_("Applicant's notes on their availability for shifts."),
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Phone / Mobile"),
        help_text=_("Optional contact number for shift coordination."),
    )
    attendance_confirmed = models.BooleanField(
        null=True,
        blank=True,
        verbose_name=_("Attendance confirmed"),
        help_text=_("Whether the team member has confirmed they will attend. None = not yet responded, True = confirmed, False = declined."),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Applied At"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))

    objects = ScopedManager(event="event")

    class Meta:
        verbose_name = _("Team Member Application")
        verbose_name_plural = _("Team Member Applications")
        unique_together = ("event", "user", "role")

    def clean(self):
        if self.role_id and self.event_id and self.role.event_id != self.event_id:
            raise ValidationError({"role": _("The selected role does not belong to this event.")})

    def __str__(self):
        return f"{self.user.email} → {self.role.name} ({self.get_status_display()})"


class Shift(models.Model):
    event = models.ForeignKey(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="shifts",
    )
    role = models.ForeignKey(
        TeamRole,
        on_delete=models.CASCADE,
        related_name="shifts",
    )
    name = models.CharField(max_length=190, blank=True, verbose_name=_("Shift Name"))
    location = models.CharField(max_length=190, blank=True, verbose_name=_("Location"))
    start_time = models.DateTimeField(verbose_name=_("Start Time"))
    end_time = models.DateTimeField(verbose_name=_("End Time"))
    capacity = models.PositiveIntegerField(default=1, verbose_name=_("Capacity"))
    description = models.TextField(blank=True, verbose_name=_("Description"))

    objects = ScopedManager(event="event")

    class Meta:
        verbose_name = _("Shift")
        verbose_name_plural = _("Shifts")
        ordering = ["start_time"]

    def clean(self):
        if self.role_id and self.event_id and self.role.event_id != self.event_id:
            raise ValidationError({"role": _("The selected role does not belong to this event.")})
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("End time must be after start time.")})

    def __str__(self):
        label = self.name or self.role.name
        return f"{label} ({self.start_time:%Y-%m-%d %H:%M} – {self.end_time:%H:%M})"

    @property
    def filled_count(self):
        return self.assignments.count()

    @property
    def is_full(self):
        return self.filled_count >= self.capacity


class ShiftAssignment(models.Model):
    shift = models.ForeignKey(
        Shift,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    team_member = models.ForeignKey(
        "base.User",
        on_delete=models.CASCADE,
        related_name="shift_assignments",
    )
    assigned_by = models.ForeignKey(
        "base.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_made",
    )
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Assigned At"))
    is_moderator = models.BooleanField(default=False, verbose_name=_("Is Moderator"))
    notified = models.BooleanField(default=False, verbose_name=_("Notified"))

    objects = ScopedManager(event="shift__event")

    class Meta:
        verbose_name = _("Shift Assignment")
        verbose_name_plural = _("Shift Assignments")
        unique_together = ("shift", "team_member")

    def __str__(self):
        return f"{self.team_member.email} → {self.shift}"


class QuestionVariant(models.TextChoices):
    STRING = "string", _("Text (one line)")
    TEXT = "text", _("Multi-line text")
    NUMBER = "number", _("Number")
    BOOLEAN = "boolean", _("Confirmation (checkbox)")
    DATE = "date", _("Date")
    DATETIME = "datetime", _("Date and time")
    URL = "url", _("URL")
    CHOICES = "choices", _("Radio button (choose one option)")
    CHOICES_DROPDOWN = "choices_dropdown", _("Dropdown (choose one option)")
    MULTIPLE = "multiple_choice", _("Checkbox (choose one or more options)")
    COUNTRY = "country", _("Country")
    PHONE = "phone", _("Phone number")


class TeamApplicationQuestion(models.Model):
    event = models.ForeignKey(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="team_application_questions",
    )
    role = models.ForeignKey(
        TeamRole,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="application_questions",
        help_text=_("Leave blank to ask this question for every role."),
    )
    question = I18nTextField(verbose_name=_("Question"))
    help_text = I18nTextField(verbose_name=_("Help text"), blank=True, null=True)
    variant = models.CharField(
        max_length=20,
        choices=QuestionVariant.choices,
        default=QuestionVariant.STRING,
        verbose_name=_("Field type"),
    )
    required = models.BooleanField(default=False, verbose_name=_("Required"))
    options = models.TextField(
        blank=True,
        verbose_name=_("Options"),
        help_text=_("One option per line. Only used for choice / multiple choice fields."),
    )
    active = models.BooleanField(default=True, verbose_name=_("Active"))

    objects = ScopedManager(event="event")

    class Meta:
        verbose_name = _("Application Question")
        verbose_name_plural = _("Application Questions")
        ordering = ["pk"]

    def clean(self):
        if self.role_id and self.event_id and self.role.event_id != self.event_id:
            raise ValidationError({"role": _("The selected role does not belong to this event.")})

    def get_options(self):
        """Return the options list for choice-style variants."""
        needs_options = (QuestionVariant.CHOICES, QuestionVariant.CHOICES_DROPDOWN, QuestionVariant.MULTIPLE)
        if self.variant not in needs_options:
            return []
        return [line.strip() for line in (self.options or "").splitlines() if line.strip()]

    def __str__(self):
        return f"{self.question} ({self.get_variant_display()})"


class TeamApplicationAnswer(models.Model):
    """
    The applicant's answer to a single :class:`TeamApplicationQuestion`.

    The raw answer is stored as text. For ``MULTIPLE`` variants the value is a
    newline-separated list of selected option labels. For ``BOOLEAN`` it is
    "true" or "false".
    """

    application = models.ForeignKey(
        TeamMemberApplication,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        TeamApplicationQuestion,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    answer = models.TextField(blank=True, verbose_name=_("Answer"))

    objects = ScopedManager(event="application__event")

    class Meta:
        verbose_name = _("Application Answer")
        verbose_name_plural = _("Application Answers")
        unique_together = ("application", "question")

    def __str__(self):
        return f"{self.application} → {self.question}: {self.answer[:30]}"


class TeamShiftsEmailQueue(models.Model):
    event = models.ForeignKey(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="teamshifts_email_queue",
    )
    user = models.ForeignKey(
        "base.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    subject = I18nTextField()
    message = I18nTextField()
    reply_to = models.CharField(max_length=200, blank=True, default="")
    bcc = models.TextField(blank=True, default="")
    locale = models.CharField(max_length=16, blank=True, default="")
    role_filter = models.ForeignKey(
        TeamRole,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status_filter = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        blank=True,
        default="",
    )
    send_after = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    objects = ScopedManager(event="event")

    class Meta:
        verbose_name = _("Queued email")
        verbose_name_plural = _("Queued emails")
        ordering = ["-created"]

    def __str__(self):
        state = "sent" if self.sent_at else ("scheduled" if self.send_after else "pending")
        return f"{self.event.slug} · {state} · {self.subject}"


class TeamShiftsEmailQueueRecipient(models.Model):
    queue = models.ForeignKey(
        TeamShiftsEmailQueue,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    user = models.ForeignKey(
        "base.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    email = models.EmailField()
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    objects = ScopedManager(event="queue__event")

    class Meta:
        verbose_name = _("Email recipient")
        verbose_name_plural = _("Email recipients")
        unique_together = ("queue", "email")

    def __str__(self):
        return f"{self.email} ({'sent' if self.sent_at else 'pending'})"

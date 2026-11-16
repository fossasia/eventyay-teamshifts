from datetime import datetime
from urllib.parse import urlencode, urlparse

import vobject
from django.conf import settings
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.translation import gettext as _, gettext_lazy
from django_scopes import scope
from eventyay.base.exporter import ListExporter
from eventyay.common.exporter import BaseExporter
from eventyay.common.urls import EventUrls
from eventyay.schedule.exporters import BaseCalendarExporter

from .forms import render_answer_for_review
from .models import CallForTeamMembers, QuestionVariant, TeamApplicationAnswer, TeamApplicationQuestion

EXPORT_FORMAT_XLSX = "xlsx"
EXPORT_FORMAT_CSV = "default"
EXPORT_FORMATS = (EXPORT_FORMAT_XLSX, EXPORT_FORMAT_CSV)

DATETIME_ANSWER_FORMAT = "%Y-%m-%d %H:%M"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def get_export_questions(event) -> list[TeamApplicationQuestion]:
    with scope(event=event):
        questions = list(TeamApplicationQuestion.objects.filter(event=event, active=True))
        try:
            field_order = event.call_for_team_members.field_order
        except CallForTeamMembers.DoesNotExist:
            field_order = []
    positions = {item: index for index, item in enumerate(field_order) if isinstance(item, int)}
    fallback = len(positions)
    return sorted(questions, key=lambda question: (positions.get(question.pk, fallback), question.pk))


def get_answer_map(event, member_ids) -> dict[tuple[int, int], str]:
    with scope(event=event):
        rows = TeamApplicationAnswer.objects.filter(application_id__in=member_ids).values_list("application_id", "question_id", "answer")
        return {(application_id, question_id): answer for application_id, question_id, answer in rows}


def format_answer(question: TeamApplicationQuestion, answer_text: str, tz) -> str:
    if not answer_text:
        return ""
    if question.variant == QuestionVariant.DATETIME:
        try:
            parsed = datetime.fromisoformat(answer_text)
        except ValueError:
            return answer_text
        if parsed.tzinfo is None:
            return answer_text
        return parsed.astimezone(tz).strftime(DATETIME_ANSWER_FORMAT)
    return str(render_answer_for_review(question, answer_text))


def format_yes_no(value: bool) -> str:
    return _("Yes") if value else _("No")


def format_hours(duration) -> float:
    if not duration:
        return 0
    return round(duration.total_seconds() / 3600, 2)


def get_member_roles(member) -> str:
    names = []
    for assignment in getattr(member.user, "event_assignments", []):
        if assignment.role and assignment.role.name not in names:
            names.append(assignment.role.name)
    return ", ".join(names)


def neutralize_formula(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


class MembersExporter(ListExporter):
    identifier = "teamshifts_members"
    verbose_name = gettext_lazy("Members")

    def __init__(self, event, members, progress_callback=lambda v: None):
        super().__init__(event, progress_callback)
        self.members = members

    @property
    def export_form_fields(self) -> dict:
        fields = super().export_form_fields
        fields["_format"].choices = [(value, label) for value, label in fields["_format"].choices if value in EXPORT_FORMATS]
        return fields

    def get_filename(self):
        today = datetime.now(self.event.tz).strftime("%Y-%m-%d")
        return f"{self.event.slug}-members-{today}"

    def get_header(self, questions) -> list[str]:
        fixed = [
            _("Name"),
            _("Email"),
            _("Phone"),
            _("Role(s)"),
            _("Shifts assigned"),
            _("Hours scheduled"),
            _("Arrived"),
            _("Availability notes"),
            _("Application status"),
            _("Date applied"),
            _("Added by organizer"),
        ]
        return [str(column) for column in fixed] + [str(question.question) for question in questions]

    def get_row(self, member, questions, answers, tz) -> list:
        row = [
            member.user.fullname or member.user.email,
            member.user.email,
            member.phone,
            get_member_roles(member),
            member.shifts_assigned,
            format_hours(member.hours_scheduled),
            format_yes_no(member.arrived),
            member.availability_notes,
            str(member.get_status_display()),
            member.created_at.astimezone(tz).strftime(DATETIME_ANSWER_FORMAT),
            format_yes_no(member.added_by_organizer),
        ]
        row.extend(format_answer(question, answers.get((member.pk, question.pk), ""), tz) for question in questions)
        return row

    def iterate_list(self, form_data):
        tz = self.event.tz
        questions = get_export_questions(self.event)
        answers = get_answer_map(self.event, [member.pk for member in self.members])
        guard = neutralize_formula if form_data.get("_format") == EXPORT_FORMAT_XLSX else lambda value: value
        yield self.ProgressSetTotal(total=len(self.members))
        yield [guard(value) for value in self.get_header(questions)]
        for member in self.members:
            yield [guard(value) for value in self.get_row(member, questions, answers, tz)]


def my_shifts_export_url(url_name):
    def build(exporter):
        return f"{reverse(url_name)}?{urlencode({'event': exporter.event.pk})}"

    return build


class MyShiftsICalExporter(BaseExporter):
    identifier = "my-shifts.ics"
    verbose_name = gettext_lazy("iCal (your shifts)")
    public = False
    show_public = True
    show_qrcode = True
    icon = "fa-calendar"

    def __init__(self, event, assignments=None):
        super().__init__(event)
        self.assignments = assignments or []

    class urls(EventUrls):
        base = my_shifts_export_url("plugins:teamshifts:my_shifts_calendar_download")

    def render(self, **kwargs):
        netloc = urlparse(settings.SITE_URL).netloc
        cal = vobject.iCalendar()
        cal.add("prodid").value = f"-//eventyay//{netloc}//teamshifts//my-shifts"

        for assignment in self.assignments:
            assignment.build_ical(cal, netloc=netloc)
        file_name = f"{self.event.slug}-my-shifts.ics" if self.event else "my-shifts.ics"
        return file_name, "text/calendar", cal.serialize()


class MyShiftsGoogleCalendarExporter(BaseCalendarExporter):
    identifier = "my-google-calendar"
    verbose_name = gettext_lazy("Subscribe to My Shifts in Google Calendar")
    icon = "fa-google"
    ical_exporter_cls = MyShiftsICalExporter

    class urls(EventUrls):
        base = my_shifts_export_url("plugins:teamshifts:my_shifts_calendar_google")


class MyShiftsWebcalExporter(BaseCalendarExporter):
    identifier = "my-webcal"
    verbose_name = gettext_lazy("Subscribe to My Shifts in Other Calendar")
    ical_exporter_cls = MyShiftsICalExporter

    class urls(EventUrls):
        base = my_shifts_export_url("plugins:teamshifts:my_shifts_calendar_webcal")


SHIFT_SCHEDULE_EXPORTERS = (MyShiftsGoogleCalendarExporter, MyShiftsWebcalExporter, MyShiftsICalExporter)


def build_shift_schedule_exporters(event):
    result = []
    for exporter_cls in SHIFT_SCHEDULE_EXPORTERS:
        exporter = exporter_cls(event)
        if not exporter.show_public:
            continue
        result.append(
            {
                "identifier": exporter.identifier,
                "verbose_name": force_str(exporter.verbose_name),
                "icon": getattr(exporter, "icon", ""),
                "export_url": exporter.urls.base,
                "qrcode_svg": str(exporter.get_qrcode()) if getattr(exporter, "show_qrcode", False) else "",
            }
        )
    return result

import io
from datetime import datetime

from defusedcsv import csv
from django.utils.translation import gettext as _
from django_scopes import scope
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from ..forms import render_answer_for_review
from ..models import CallForTeamMembers, QuestionVariant, TeamApplicationAnswer, TeamApplicationQuestion

EXPORT_FORMAT_XLSX = "xlsx"
EXPORT_FORMAT_CSV = "csv"
EXPORT_FORMATS = {
    EXPORT_FORMAT_XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    EXPORT_FORMAT_CSV: "text/csv; charset=utf-8",
}

XLSX_CELL_LIMIT = 32767
DATETIME_ANSWER_FORMAT = "%Y-%m-%d %H:%M"


def get_export_questions(event) -> list[TeamApplicationQuestion]:
    """Return every question of the event, including inactive ones, so stored answers are never dropped."""
    with scope(event=event):
        questions = list(TeamApplicationQuestion.objects.filter(event=event))
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


def build_header(questions) -> list[str]:
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


def build_rows(event, members, questions) -> list[list]:
    tz = event.tz
    answers = get_answer_map(event, [member.pk for member in members])
    rows = []
    for member in members:
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
        rows.append(row)
    return rows


def render_csv(header, rows) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_NONNUMERIC, delimiter=",")
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _make_cell(worksheet, value, font=None):
    if isinstance(value, str):
        cell = WriteOnlyCell(worksheet, value=ILLEGAL_CHARACTERS_RE.sub("", value)[:XLSX_CELL_LIMIT])
        cell.data_type = "s"
    else:
        cell = WriteOnlyCell(worksheet, value=value)
    if font:
        cell.font = font
    return cell


def render_xlsx(header, rows) -> bytes:
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(str(_("Members")))
    worksheet.freeze_panes = "A2"
    bold = Font(bold=True)
    worksheet.append([_make_cell(worksheet, value, font=bold) for value in header])
    for row in rows:
        worksheet.append([_make_cell(worksheet, value) for value in row])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def render_export(export_format, header, rows) -> bytes:
    if export_format == EXPORT_FORMAT_XLSX:
        return render_xlsx(header, rows)
    return render_csv(header, rows)


def export_filename(event, export_format) -> str:
    today = datetime.now(event.tz).strftime("%Y-%m-%d")
    return f"{event.slug}-members-{today}.{export_format}"

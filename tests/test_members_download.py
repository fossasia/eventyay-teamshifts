import io
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team
from openpyxl import load_workbook

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    QuestionVariant,
    Shift,
    ShiftAssignment,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
    TeamRole,
)
from teamshifts.services.members_export import format_answer, format_hours, render_csv, render_xlsx


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True)


@pytest.fixture
def orga_user(event, user):
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, name="Orga", can_change_event_settings=True, all_events=True)
        team.members.add(user)
    return user


@pytest.fixture
def lead_user(event, django_user_model):
    lead = django_user_model.objects.create_user(email="lead@example.com", password="secret")
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, name="Leads", all_events=True, teamshifts_role="lead")
        team.members.add(lead)
    return lead


def _url(event):
    return reverse("plugins:teamshifts:members_download", kwargs={"organizer": event.organizer.slug, "event": event.slug})


def _member(event, django_user_model, email, fullname="", **kwargs):
    applicant = django_user_model.objects.create_user(email=email, password="x", fullname=fullname)
    return TeamMemberApplication.objects.create(event=event, user=applicant, status=ApplicationStatus.ACCEPTED, **kwargs)


def _rows(response):
    workbook = load_workbook(io.BytesIO(response.content))
    return [[cell.value for cell in row] for row in workbook.active.iter_rows()]


def test_format_answer_boolean_and_multiple():
    boolean = TeamApplicationQuestion(variant=QuestionVariant.BOOLEAN)
    multiple = TeamApplicationQuestion(variant=QuestionVariant.MULTIPLE)
    tz = ZoneInfo("UTC")
    assert format_answer(boolean, "true", tz) == "Yes"
    assert format_answer(boolean, "false", tz) == "No"
    assert format_answer(multiple, "S\nM\nL", tz) == "S, M, L"
    assert format_answer(multiple, "", tz) == ""


def test_format_answer_datetime_uses_event_timezone():
    question = TeamApplicationQuestion(variant=QuestionVariant.DATETIME)
    assert format_answer(question, "2026-10-03T10:00:00+00:00", ZoneInfo("Asia/Kolkata")) == "2026-10-03 15:30"
    assert format_answer(question, "2026-10-03 10:00", ZoneInfo("Asia/Kolkata")) == "2026-10-03 10:00"


def test_format_hours():
    assert format_hours(None) == 0
    assert format_hours(timedelta(hours=2, minutes=30)) == 2.5


def test_csv_escapes_formulas():
    payload = render_csv(["Name"], [["=cmd()"]]).decode("utf-8-sig")
    assert "=cmd()" not in payload.replace("'=cmd()", "")


def test_xlsx_stores_formula_like_text_as_string():
    workbook = load_workbook(io.BytesIO(render_xlsx(["Name"], [["=1+1"]])))
    cell = workbook.active["A2"]
    assert cell.value == "=1+1"
    assert cell.data_type == "s"


@pytest.mark.django_db
def test_download_xlsx_contains_members_and_answers(client, event, cfm, orga_user, django_user_model):
    with scope(event=event):
        role = TeamRole.objects.create(event=event, name="Volunteer")
        tshirt = TeamApplicationQuestion.objects.create(event=event, question="T-shirt", variant=QuestionVariant.MULTIPLE, options="S\nM", active=False)
        agree = TeamApplicationQuestion.objects.create(event=event, question="Agree", variant=QuestionVariant.BOOLEAN)
        member = _member(event, django_user_model, "a@example.com", "Alice", phone="123", arrived=True, added_by_organizer=True)
        _member(event, django_user_model, "b@example.com", "Bob")
        TeamApplicationAnswer.objects.create(application=member, question=tshirt, answer="S\nM")
        TeamApplicationAnswer.objects.create(application=member, question=agree, answer="true")
        start = now()
        shift = Shift.objects.create(event=event, name="Desk", start_time=start, end_time=start + timedelta(hours=2))
        ShiftAssignment.objects.create(shift=shift, team_member=member.user, role=role)

    client.force_login(orga_user)
    response = client.get(_url(event), {"format": "xlsx"})
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith(f'attachment; filename="{event.slug}-members-')
    assert response["Content-Disposition"].endswith('.xlsx"')
    rows = _rows(response)
    header = rows[0]
    assert header[:2] == ["Name", "Email"]
    assert header[-2:] == ["T-shirt", "Agree"]
    alice = next(row for row in rows[1:] if row[1] == "a@example.com")
    assert alice[0] == "Alice"
    assert alice[2] == "123"
    assert alice[3] == "Volunteer"
    assert alice[4] == 1
    assert alice[5] == 2
    assert alice[6] == "Yes"
    assert alice[10] == "Yes"
    assert alice[-2:] == ["S, M", "Yes"]
    bob = next(row for row in rows[1:] if row[1] == "b@example.com")
    assert bob[-2:] == [None, None]
    assert bob[10] == "No"


@pytest.mark.django_db
def test_download_csv_respects_search_filter(client, event, cfm, orga_user, django_user_model):
    with scope(event=event):
        _member(event, django_user_model, "a@example.com", "Alice")
        _member(event, django_user_model, "b@example.com", "Bob")
    client.force_login(orga_user)
    response = client.get(_url(event), {"format": "csv", "q": "alice"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = response.content.decode("utf-8-sig")
    assert "a@example.com" in body
    assert "b@example.com" not in body


@pytest.mark.django_db
def test_download_role_filter_limits_members(client, event, cfm, orga_user, django_user_model):
    with scope(event=event):
        role = TeamRole.objects.create(event=event, name="Volunteer")
        member = _member(event, django_user_model, "a@example.com", "Alice")
        _member(event, django_user_model, "b@example.com", "Bob")
        start = now()
        shift = Shift.objects.create(event=event, name="Desk", start_time=start, end_time=start + timedelta(hours=1))
        ShiftAssignment.objects.create(shift=shift, team_member=member.user, role=role)
    client.force_login(orga_user)
    response = client.get(_url(event), {"format": "csv", "role": role.pk})
    body = response.content.decode("utf-8-sig")
    assert "a@example.com" in body
    assert "b@example.com" not in body


@pytest.mark.django_db
def test_download_rejects_unknown_format(client, event, cfm, orga_user):
    client.force_login(orga_user)
    assert client.get(_url(event), {"format": "pdf"}).status_code == 400


@pytest.mark.django_db
def test_download_forbidden_for_team_lead(client, event, cfm, lead_user):
    client.force_login(lead_user)
    assert client.get(_url(event), {"format": "csv"}).status_code == 403


@pytest.mark.django_db
def test_download_requires_login(client, event, cfm):
    assert client.get(_url(event), {"format": "csv"}).status_code in (302, 403)


@pytest.mark.django_db
def test_members_page_shows_download_only_to_coordinators(client, event, cfm, orga_user, lead_user):
    members_url = reverse("plugins:teamshifts:members", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    client.force_login(orga_user)
    assert "format=xlsx" in client.get(members_url).content.decode()
    client.force_login(lead_user)
    assert "format=xlsx" not in client.get(members_url).content.decode()

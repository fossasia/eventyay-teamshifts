from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    QuestionVariant,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
)


def _team_client(client, event, user, settings, **team_kwargs):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        team = Team.objects.create(organizer=event.organizer, all_events=True, **team_kwargs)
        team.members.add(user)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_client(client, event, user, settings):
    return _team_client(client, event, user, settings, name="Orga Team", can_change_event_settings=True)


@pytest.fixture
def hidden_email_lead_client(client, event, user, settings):
    return _team_client(client, event, user, settings, name="Leads", teamshifts_role="lead", hide_teamshifts_emails=True)


@pytest.fixture
def cfm(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True)


def _url(event):
    return reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug})


def _apply(event, name, email, **fields):
    """Create an application; each one is a minute newer than the last so the default order is predictable."""
    with scope(event=event):
        user = User.objects.create_user(email=email, password="x", fullname=name)
        application = TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.PENDING, **fields)
        offset = TeamMemberApplication.objects.filter(event=event).count()
        TeamMemberApplication.objects.filter(pk=application.pk).update(created_at=now() - timedelta(days=1) + timedelta(minutes=offset))
        return application


def _question(event, cfm, variant, label="Question"):
    """Create a question and show it as the second column, before email and phone."""
    with scope(event=event):
        question = TeamApplicationQuestion.objects.create(event=event, question=label, variant=variant, active=True)
        cfm.field_order = ["full_name", question.pk, "email", "phone", "availability"]
        cfm.save(update_fields=["field_order"])
        return question


def _answer(event, application, question, answer):
    with scope(event=event):
        TeamApplicationAnswer.objects.create(application=application, question=question, answer=answer)


def _names(client, event, sort):
    response = client.get(_url(event), {"sort": sort})
    assert response.status_code == 200
    return [app.user.fullname for app in response.context["applications"]], response


def _assert_sorted(client, event, sort, ascending):
    """Check the order both ways: descending reverses the answers but keeps empty answers last."""
    names, response = _names(client, event, sort)
    assert names == ascending
    assert response.context["current_ordering"] == sort
    return response


@pytest.mark.django_db
def test_email_column_sorts_both_ways(coordinator_client, event, cfm):
    _apply(event, "Carol", "carol@example.com")
    _apply(event, "Alice", "Alice@example.com")
    _apply(event, "Bob", "bob@example.com")

    _assert_sorted(coordinator_client, event, "user__email", ["Alice", "Bob", "Carol"])
    _assert_sorted(coordinator_client, event, "-user__email", ["Carol", "Bob", "Alice"])


@pytest.mark.django_db
def test_builtin_phone_column_sorts_with_empty_values_last(coordinator_client, event, cfm):
    _apply(event, "No Phone", "none@example.com", phone="")
    _apply(event, "Second", "second@example.com", phone="+4930222")
    _apply(event, "First", "first@example.com", phone="+4930111")

    _assert_sorted(coordinator_client, event, "phone", ["First", "Second", "No Phone"])
    _assert_sorted(coordinator_client, event, "-phone", ["Second", "First", "No Phone"])


@pytest.mark.django_db
def test_availability_column_sorts_alphabetically_ignoring_case(coordinator_client, event, cfm):
    cfm.field_order = ["full_name", "availability", "email", "phone"]
    cfm.save(update_fields=["field_order"])
    _apply(event, "Weekend", "weekend@example.com", availability_notes="weekends")
    _apply(event, "Evening", "evening@example.com", availability_notes="Evenings")
    _apply(event, "Anytime", "anytime@example.com", availability_notes="anytime")

    _assert_sorted(coordinator_client, event, "availability_notes", ["Anytime", "Evening", "Weekend"])
    _assert_sorted(coordinator_client, event, "-availability_notes", ["Weekend", "Evening", "Anytime"])


@pytest.mark.django_db
def test_text_question_sorts_alphabetically_with_unanswered_last(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.STRING, "Do you live in Mumbai?")
    yes = _apply(event, "Yes Person", "yes@example.com")
    no = _apply(event, "No Person", "no@example.com")
    _apply(event, "Unanswered", "unanswered@example.com")
    blank = _apply(event, "Blank", "blank@example.com")
    _answer(event, yes, question, "yes")
    _answer(event, no, question, "No")
    _answer(event, blank, question, "")

    sort_key = f"question_{question.pk}"
    _assert_sorted(coordinator_client, event, sort_key, ["No Person", "Yes Person", "Blank", "Unanswered"])
    _assert_sorted(coordinator_client, event, f"-{sort_key}", ["Yes Person", "No Person", "Blank", "Unanswered"])


@pytest.mark.django_db
def test_choice_question_groups_same_answers_and_orders_them_by_name(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.CHOICES_DROPDOWN, "T-shirt size")
    for name, answer in (("Zoe", "Medium"), ("Adam", "Small"), ("Mia", "Medium"), ("Ben", "Large")):
        _answer(event, _apply(event, name, f"{name.lower()}@example.com"), question, answer)

    sort_key = f"question_{question.pk}"
    _assert_sorted(coordinator_client, event, sort_key, ["Ben", "Mia", "Zoe", "Adam"])
    _assert_sorted(coordinator_client, event, f"-{sort_key}", ["Adam", "Mia", "Zoe", "Ben"])


@pytest.mark.django_db
def test_number_question_sorts_numerically(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.NUMBER, "Years of experience")
    for name, answer in (("Ten", "10"), ("Two", "2"), ("Half", "0.5"), ("Minus", "-1")):
        _answer(event, _apply(event, name, f"{name.lower()}@example.com"), question, answer)
    _apply(event, "Unanswered", "unanswered@example.com")
    # An answer that is not a number (e.g. saved before the question type changed) must not break sorting.
    _answer(event, _apply(event, "Words", "words@example.com"), question, "a few")

    sort_key = f"question_{question.pk}"
    _assert_sorted(coordinator_client, event, sort_key, ["Minus", "Half", "Two", "Ten", "Unanswered", "Words"])
    _assert_sorted(coordinator_client, event, f"-{sort_key}", ["Ten", "Two", "Half", "Minus", "Unanswered", "Words"])


@pytest.mark.django_db
def test_yes_no_question_sorts_no_before_yes(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.BOOLEAN, "Has a car")
    _answer(event, _apply(event, "Driver", "driver@example.com"), question, "true")
    _answer(event, _apply(event, "Walker", "walker@example.com"), question, "false")
    _apply(event, "Unanswered", "unanswered@example.com")

    sort_key = f"question_{question.pk}"
    _assert_sorted(coordinator_client, event, sort_key, ["Walker", "Driver", "Unanswered"])
    _assert_sorted(coordinator_client, event, f"-{sort_key}", ["Driver", "Walker", "Unanswered"])


@pytest.mark.django_db
def test_date_question_sorts_chronologically(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.DATE, "Arrival date")
    for name, answer in (("March", "2026-03-01"), ("January", "2026-01-15"), ("Last year", "2025-12-31")):
        _answer(event, _apply(event, name, f"{name.lower().replace(' ', '')}@example.com"), question, answer)

    sort_key = f"question_{question.pk}"
    _assert_sorted(coordinator_client, event, sort_key, ["Last year", "January", "March"])
    _assert_sorted(coordinator_client, event, f"-{sort_key}", ["March", "January", "Last year"])


@pytest.mark.django_db
def test_multiple_choice_question_sorts_by_shown_text(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.MULTIPLE, "Languages")
    _answer(event, _apply(event, "Both", "both@example.com"), question, "English\nGerman")
    _answer(event, _apply(event, "German", "german@example.com"), question, "German")
    _answer(event, _apply(event, "English", "english@example.com"), question, "English")

    sort_key = f"question_{question.pk}"
    _assert_sorted(coordinator_client, event, sort_key, ["English", "Both", "German"])
    _assert_sorted(coordinator_client, event, f"-{sort_key}", ["German", "Both", "English"])


@pytest.mark.django_db
def test_every_shown_column_gets_sort_links(coordinator_client, event, cfm):
    question = _question(event, cfm, QuestionVariant.STRING, "Newly added question")
    _apply(event, "Alice", "alice@example.com")

    response = coordinator_client.get(_url(event))
    content = response.content.decode()

    assert [col["sort_key"] for col in response.context["columns"]] == ["user__fullname", f"question_{question.pk}", "user__email", "phone"]
    for sort_key in ("user__fullname", f"question_{question.pk}", "user__email", "phone", "status", "created_at"):
        assert f"sort={sort_key}&amp;" in content
        assert f"sort=-{sort_key}&amp;" in content
    assert "Sort Newly added question ascending" in content
    assert "Sort Newly added question descending" in content


@pytest.mark.django_db
def test_columns_that_are_not_shown_fall_back_to_the_default_order(coordinator_client, event, cfm):
    hidden = _question(event, cfm, QuestionVariant.STRING, "Hidden")
    with scope(event=event):
        hidden.active = False
        hidden.save(update_fields=["active"])
    older = _apply(event, "Older", "older@example.com")
    newer = _apply(event, "Newer", "newer@example.com")
    _answer(event, older, hidden, "a")
    _answer(event, newer, hidden, "b")

    names, response = _names(coordinator_client, event, f"question_{hidden.pk}")

    assert names == ["Newer", "Older"]
    assert response.context["current_ordering"] == "-created_at"


@pytest.mark.django_db
def test_email_is_not_sortable_when_emails_are_hidden(hidden_email_lead_client, event, cfm):
    _apply(event, "Older", "a@example.com")
    _apply(event, "Newer", "z@example.com")

    names, response = _names(hidden_email_lead_client, event, "user__email")

    assert "user__email" not in [col["sort_key"] for col in response.context["columns"]]
    assert names == ["Newer", "Older"]
    assert response.context["current_ordering"] == "-created_at"
    content = response.content.decode()
    assert "Sort Email ascending" not in content
    assert "Sort Email descending" not in content


@pytest.mark.django_db
def test_sidebar_calls_the_page_applications(coordinator_client, event, cfm):
    content = coordinator_client.get(_url(event)).content.decode()

    assert '<span class="sidebar-text">Applications</span>' in content
    assert "Applicants" not in content

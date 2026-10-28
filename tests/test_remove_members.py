from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, Team, User, Voucher

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    MemberVoucher,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    TeamMemberApplication,
    TeamRole,
    VoucherStatus,
)


@pytest.fixture
def event(db):
    organizer = Organizer.objects.create(name="FOSSASIA", slug="fossasia")
    event = Event.objects.create(
        name="FOSSASIA Summit",
        slug="fossasia-summit",
        organizer=organizer,
        date_from=now(),
        date_to=now() + timedelta(days=5),
        live=True,
        plugins="teamshifts",
    )
    with scopes_disabled():
        CallForTeamMembers.objects.create(event=event, active=True, shift_schedule_published=True)
    return event


@pytest.fixture
def location(event):
    with scopes_disabled():
        return ShiftLocation.objects.create(event=event, name="Main Hall")


@pytest.fixture
def role(event):
    with scopes_disabled():
        return TeamRole.objects.create(event=event, name="Registration")


def _make_shift(event, location, name, start_offset):
    start = now() + start_offset
    with scopes_disabled():
        return Shift.objects.create(event=event, name=name, location=location, start_time=start, end_time=start + timedelta(hours=2))


@pytest.fixture
def past_shift(event, location):
    return _make_shift(event, location, "Past shift", timedelta(days=-1))


@pytest.fixture
def upcoming_shift(event, location):
    return _make_shift(event, location, "Upcoming shift", timedelta(days=1))


@pytest.fixture
def member(event, role, past_shift, upcoming_shift):
    user = User.objects.create_user(email="volunteer@example.com", password="password", fullname="Vera Volunteer")
    with scopes_disabled():
        application = TeamMemberApplication.objects.create(event=event, user=user, status=ApplicationStatus.ACCEPTED)
        ShiftAssignment.objects.create(
            shift=past_shift,
            team_member=user,
            role=role,
            started_at=past_shift.start_time,
            ended_at=past_shift.end_time,
        )
        ShiftAssignment.objects.create(shift=upcoming_shift, team_member=user, role=role)
    return application


def _login_with_team(client, event, email="orga@example.com", **team_kwargs):
    user = User.objects.create_user(email=email, password="password")
    with scopes_disabled():
        team = Team.objects.create(organizer=event.organizer, name=email, all_events=True, **team_kwargs)
        team.members.add(user)
    client.force_login(user)
    return user


def _url(name, event, **extra):
    return reverse(f"plugins:teamshifts:{name}", kwargs={"organizer": event.organizer.slug, "event": event.slug, **extra})


def _remove(client, event, applications, **options):
    data = {"member_ids": [str(app.pk) for app in applications], **options}
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(_url("bulk_remove_members", event), data)


@pytest.fixture(autouse=True)
def https_site(settings):
    settings.SITE_URL = "https://testserver"


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_remove_frees_upcoming_spots_and_keeps_shift_history(mock_queue, client, event, member, past_shift, upcoming_shift):
    _login_with_team(client, event, teamshifts_role="coordinator")

    response = _remove(client, event, [member], notify="on")

    assert response.status_code == 302
    with scopes_disabled():
        member.refresh_from_db()
        assert member.status == ApplicationStatus.REMOVED
        kept = ShiftAssignment.objects.get(team_member=member.user)
        assert kept.shift == past_shift
        assert kept.started_at is not None
        assert kept.ended_at is not None
        assert not ShiftAssignment.objects.filter(shift=upcoming_shift).exists()
    messages = [str(m) for m in response.wsgi_request._messages]
    assert messages == ["1 member removed from the team. 1 shift spot freed up. 1 notification email sent."]


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_remove_keeps_shift_the_member_already_checked_in_to(mock_queue, client, event, member, upcoming_shift):
    with scopes_disabled():
        ShiftAssignment.objects.filter(shift=upcoming_shift).update(started_at=now())
    _login_with_team(client, event, teamshifts_role="coordinator")

    _remove(client, event, [member])

    with scopes_disabled():
        assert ShiftAssignment.objects.filter(shift=upcoming_shift, team_member=member.user).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(("notify", "expected_calls"), [("on", 1), (None, 0)])
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_remove_sends_removed_email_only_when_asked(mock_queue, client, event, member, notify, expected_calls):
    _login_with_team(client, event, teamshifts_role="coordinator")

    _remove(client, event, [member], **({"notify": notify} if notify else {}))

    assert mock_queue.call_count == expected_calls
    for call in mock_queue.call_args_list:
        assert call.args[1] == EmailTemplateRoles.MEMBER_REMOVED


@pytest.mark.django_db
@patch("teamshifts.views.queue_lifecycle_email")
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_remove_never_sends_rejection_email(mock_members_queue, mock_views_queue, client, event, member):
    _login_with_team(client, event, teamshifts_role="coordinator")

    _remove(client, event, [member], notify="on")

    roles = [call.args[1] for call in mock_members_queue.call_args_list + mock_views_queue.call_args_list]
    assert EmailTemplateRoles.APPLICATION_REJECTED not in roles


@pytest.mark.django_db
@pytest.mark.parametrize("redeemed", [0, 1])
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_cancel_vouchers_deletes_only_unused_codes(mock_queue, client, event, member, redeemed):
    with scopes_disabled():
        voucher = Voucher.objects.create(event=event, tag="volunteers", max_usages=1, redeemed=redeemed)
        MemberVoucher.objects.create(event=event, application=member, voucher=voucher, status=VoucherStatus.SENT)
    _login_with_team(client, event, teamshifts_role="coordinator")

    _remove(client, event, [member], cancel_vouchers="on")

    with scopes_disabled():
        assert Voucher.objects.filter(pk=voucher.pk).exists() is bool(redeemed)


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_vouchers_are_kept_without_cancel_option(mock_queue, client, event, member):
    with scopes_disabled():
        voucher = Voucher.objects.create(event=event, tag="volunteers", max_usages=1, redeemed=0)
        MemberVoucher.objects.create(event=event, application=member, voucher=voucher, status=VoucherStatus.SENT)
    _login_with_team(client, event, teamshifts_role="coordinator")

    _remove(client, event, [member])

    with scopes_disabled():
        assert Voucher.objects.filter(pk=voucher.pk).exists()


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_lead_cannot_remove_members_or_see_the_button(mock_queue, client, event, member):
    _login_with_team(client, event, teamshifts_role="lead")

    page = client.get(_url("members", event))
    assert page.status_code == 200
    assert b"bulk-remove-btn" not in page.content

    response = _remove(client, event, [member])
    assert response.status_code == 403
    with scopes_disabled():
        member.refresh_from_db()
    assert member.status == ApplicationStatus.ACCEPTED


@pytest.mark.django_db
def test_members_page_shows_disabled_button_and_upcoming_shift_count(client, event, member):
    _login_with_team(client, event, teamshifts_role="coordinator")

    page = client.get(_url("members", event))

    content = page.content.decode()
    assert "id='bulk-remove-btn' disabled" in content
    assert "data-upcoming-shifts='1'" in content


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_removed_member_leaves_members_page_and_shows_on_applications(mock_queue, client, event, member):
    with scopes_disabled():
        TeamMemberApplication.objects.filter(pk=member.pk).update(added_by_organizer=True)
    _login_with_team(client, event, teamshifts_role="coordinator")

    _remove(client, event, [member])

    assert list(client.get(_url("members", event)).context["members"]) == []
    filtered = client.get(_url("applications", event), {"status": ApplicationStatus.REMOVED})
    assert [app.pk for app in filtered.context["applications"]] == [member.pk]


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_removed_member_loses_shift_schedule_access(mock_queue, client, event, member, upcoming_shift):
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])

    client.force_login(member.user)
    schedule = client.get(_url("public_shift_schedule", event))
    assert schedule.status_code == 302
    assert schedule.url == _url("apply", event)
    apply_page = client.get(schedule.url)
    assert b"Removed from the team" in apply_page.content
    assert b"Rejected" not in apply_page.content
    assert client.get(_url("my_shifts", event)).status_code == 302
    # The past shift stays in the history, but My Shifts no longer lists the event for them.
    assert client.get(reverse("plugins:teamshifts:my_shifts_global")).status_code == 404
    claim = client.post(_url("public_shift_claim", event, pk=upcoming_shift.pk), HTTP_ACCEPT="application/json")
    assert claim.status_code == 403


@pytest.mark.django_db
@patch("teamshifts.views.queue_lifecycle_email")
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_reaccepting_removed_member_does_not_restore_old_shifts(mock_members_queue, mock_views_queue, client, event, member, upcoming_shift):
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])

    with TestCase.captureOnCommitCallbacks(execute=True):
        client.post(_url("application_status", event, pk=member.pk), {"status": ApplicationStatus.ACCEPTED})

    with scopes_disabled():
        member.refresh_from_db()
        assert member.status == ApplicationStatus.ACCEPTED
        assert not ShiftAssignment.objects.filter(shift=upcoming_shift).exists()
    assert [app.pk for app in client.get(_url("members", event)).context["members"]] == [member.pk]


@pytest.mark.django_db
def test_status_dropdown_cannot_skip_the_removal_flow(client, event, member, upcoming_shift):
    _login_with_team(client, event, teamshifts_role="coordinator")

    response = client.post(_url("application_status", event, pk=member.pk), {"status": ApplicationStatus.REMOVED})

    assert response.status_code == 400
    with scopes_disabled():
        member.refresh_from_db()
        assert member.status == ApplicationStatus.ACCEPTED
        assert ShiftAssignment.objects.filter(shift=upcoming_shift).exists()


@pytest.mark.django_db
def test_removed_from_team_template_is_editable(client, event):
    _login_with_team(client, event, teamshifts_role="coordinator")

    listing = client.get(_url("email_templates", event))
    assert listing.status_code == 200
    assert EmailTemplateRoles.MEMBER_REMOVED in [panel["role"] for panel in listing.context["panels"]]

    edit = client.get(_url("email_template_edit", event, role=EmailTemplateRoles.MEMBER_REMOVED))
    assert edit.status_code == 200
    assert "Update on your participation" in str(edit.context["template"].subject)

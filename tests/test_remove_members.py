from datetime import timedelta
from types import SimpleNamespace
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
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
    VolunteerVoucherSettings,
    VoucherStatus,
)
from teamshifts.services.calendar import get_member_assignments
from teamshifts.services.checkin import handle_volunteer_checkout
from teamshifts.services.vouchers import allocate_and_send_vouchers


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
        # The link goes with the cancelled code, so a restored member can be sent a fresh one.
        assert MemberVoucher.objects.filter(application=member).exists() is bool(redeemed)


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
def test_invalid_member_ids_are_ignored(client, event, member):
    _login_with_team(client, event, teamshifts_role="coordinator")

    response = _remove(client, event, [], member_ids=["²", "99999999999", "9" * 5000, "abc"])

    assert response.status_code == 302
    assert [str(m) for m in response.wsgi_request._messages] == ["No members selected."]
    with scopes_disabled():
        member.refresh_from_db()
    assert member.status == ApplicationStatus.ACCEPTED


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_no_email_is_counted_without_a_call_for_team_members(mock_queue, client, event, member):
    with scopes_disabled():
        CallForTeamMembers.objects.filter(event=event).delete()
    _login_with_team(client, event, teamshifts_role="coordinator")

    response = _remove(client, event, [member], notify="on")

    mock_queue.assert_not_called()
    assert [str(m) for m in response.wsgi_request._messages] == ["1 member removed from the team. 1 shift spot freed up."]


@pytest.mark.django_db
def test_failing_removal_email_does_not_break_the_request_or_other_emails(client, event, member, django_user_model):
    second_user = django_user_model.objects.create_user(email="second@example.com", password="x")
    with scopes_disabled():
        second = TeamMemberApplication.objects.create(event=event, user=second_user, status=ApplicationStatus.ACCEPTED)
    _login_with_team(client, event, teamshifts_role="coordinator")

    with patch("teamshifts.services.members.queue_lifecycle_email", side_effect=[RuntimeError("smtp down"), None]) as mock_queue:
        response = _remove(client, event, [member, second], notify="on")

    assert response.status_code == 302
    assert mock_queue.call_count == 2
    with scopes_disabled():
        assert set(TeamMemberApplication.objects.filter(pk__in=[member.pk, second.pk]).values_list("status", flat=True)) == {ApplicationStatus.REMOVED}


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_calendar_feed_leaves_out_events_the_member_was_removed_from(mock_queue, client, event, member):
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])

    with scopes_disabled():
        assert ShiftAssignment.objects.filter(team_member=member.user).exists()
    assert not get_member_assignments(member.user).exists()


@pytest.fixture
def in_progress_assignment(event, location, role, member):
    running = _make_shift(event, location, "Running shift", timedelta(hours=-1))
    with scopes_disabled():
        return ShiftAssignment.objects.create(shift=running, team_member=member.user, role=role, started_at=running.start_time)


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_member_removed_mid_shift_can_still_check_out_of_it(mock_queue, client, event, member, in_progress_assignment, past_shift):
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])
    client.force_login(member.user)

    my_shifts = client.get(reverse("plugins:teamshifts:my_shifts_global"))
    assert my_shifts.status_code == 200
    assert b"Running shift" in my_shifts.content
    assert past_shift.name.encode() not in my_shifts.content
    assert client.get(_url("public_shift_schedule", event)).status_code == 302

    response = client.post(_url("shift_checkout", event, pk=in_progress_assignment.pk), HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 200
    with scopes_disabled():
        in_progress_assignment.refresh_from_db()
    assert in_progress_assignment.ended_at is not None


@pytest.mark.django_db
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_removed_member_without_a_running_shift_cannot_use_check_out(mock_queue, client, event, member, past_shift):
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])
    client.force_login(member.user)
    with scopes_disabled():
        past_assignment = ShiftAssignment.objects.get(shift=past_shift, team_member=member.user)

    response = client.post(_url("shift_checkout", event, pk=past_assignment.pk), HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 403


@pytest.mark.django_db
@patch("teamshifts.services.checkin.maybe_auto_issue_certificate")
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_exit_scan_closes_the_running_shift_of_a_removed_member(mock_queue, mock_certificate, client, event, member, in_progress_assignment):
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])
    exit_scan = SimpleNamespace(
        list=SimpleNamespace(event=event),
        position=SimpleNamespace(voucher_id=None, attendee_email=member.user.email),
        datetime=now(),
    )

    handle_volunteer_checkout(exit_scan)

    with scopes_disabled():
        in_progress_assignment.refresh_from_db()
    assert in_progress_assignment.ended_at is not None
    mock_certificate.assert_not_called()


@pytest.mark.django_db
@patch("teamshifts.services.checkin.maybe_auto_issue_certificate")
@patch("teamshifts.services.members.queue_lifecycle_email")
def test_exit_scan_prefers_the_accepted_member_like_the_entry_scan(
    mock_queue, mock_certificate, client, event, member, in_progress_assignment, location, role, django_user_model
):
    # The ticket was bought with the removed member's voucher but is worn by an accepted member.
    accepted_user = django_user_model.objects.create_user(email="wearer@example.com", password="x")
    running = _make_shift(event, location, "Wearer shift", timedelta(hours=-1))
    with scopes_disabled():
        accepted = TeamMemberApplication.objects.create(event=event, user=accepted_user, status=ApplicationStatus.ACCEPTED)
        wearer_assignment = ShiftAssignment.objects.create(shift=running, team_member=accepted_user, role=role, started_at=running.start_time)
        voucher = Voucher.objects.create(event=event, tag="volunteers", max_usages=1, redeemed=1)
        MemberVoucher.objects.create(event=event, application=member, voucher=voucher, status=VoucherStatus.CLAIMED)
    _login_with_team(client, event, teamshifts_role="coordinator")
    _remove(client, event, [member])
    exit_scan = SimpleNamespace(
        list=SimpleNamespace(event=event),
        position=SimpleNamespace(voucher_id=voucher.pk, attendee_email=accepted_user.email),
        datetime=now(),
    )

    handle_volunteer_checkout(exit_scan)

    with scopes_disabled():
        wearer_assignment.refresh_from_db()
        in_progress_assignment.refresh_from_db()
    assert wearer_assignment.ended_at is not None
    assert in_progress_assignment.ended_at is None
    mock_certificate.assert_called_once_with(accepted)


@pytest.mark.django_db
def test_claim_is_refused_when_member_was_removed_after_the_access_check(client, event, member, role, location):
    later_shift = _make_shift(event, location, "Later shift", timedelta(days=2))
    with scopes_disabled():
        ShiftRoleAssignment.objects.create(shift=later_shift, role=role, capacity=2)
        stale_application = TeamMemberApplication.objects.get(pk=member.pk)
        TeamMemberApplication.objects.filter(pk=member.pk).update(status=ApplicationStatus.REMOVED)
    client.force_login(member.user)

    # The schedule mixin still sees the accepted application it read before the removal.
    with patch("teamshifts.views._get_accepted_application", return_value=stale_application):
        response = client.post(_url("public_shift_claim", event, pk=later_shift.pk), {"role_id": role.pk}, HTTP_ACCEPT="application/json")

    assert response.status_code == 400
    with scopes_disabled():
        assert not ShiftAssignment.objects.filter(shift=later_shift).exists()


@pytest.mark.django_db
def test_voucher_is_not_allocated_to_a_member_removed_meanwhile(event, member):
    with scopes_disabled():
        settings = VolunteerVoucherSettings.objects.create(event=event, enabled=True, voucher_tag="volunteers")
        Voucher.objects.create(event=event, tag="volunteers", max_usages=1, redeemed=0)
        stale_application = TeamMemberApplication.objects.select_related("user").get(pk=member.pk)
        TeamMemberApplication.objects.filter(pk=member.pk).update(status=ApplicationStatus.REMOVED)

    with patch("teamshifts.services.vouchers._send_voucher_email", return_value=True) as mock_send:
        result = allocate_and_send_vouchers(event, settings, [stale_application])

    assert result["sent"] == 0
    mock_send.assert_not_called()
    with scopes_disabled():
        assert not MemberVoucher.objects.filter(application=member).exists()


@pytest.mark.django_db
def test_removed_from_team_template_is_editable(client, event):
    _login_with_team(client, event, teamshifts_role="coordinator")

    listing = client.get(_url("email_templates", event))
    assert listing.status_code == 200
    assert EmailTemplateRoles.MEMBER_REMOVED in [panel["role"] for panel in listing.context["panels"]]

    edit = client.get(_url("email_template_edit", event, role=EmailTemplateRoles.MEMBER_REMOVED))
    assert edit.status_code == 200
    assert "Update on your participation" in str(edit.context["template"].subject)

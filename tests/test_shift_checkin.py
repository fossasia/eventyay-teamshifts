from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils.timezone import now
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, User

from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    MemberVoucher,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
)
from teamshifts.services.checkin import (
    end_shift,
    handle_volunteer_checkin,
    resolve_volunteer_application,
    stamp_shift_start,
)


@pytest.fixture
def organizer(db):
    return Organizer.objects.create(name="FOSSASIA", slug="fossasia")


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        name="Summit",
        slug="summit",
        organizer=organizer,
        date_from=now(),
        date_to=now() + timedelta(days=2),
        plugins="teamshifts",
    )


@pytest.fixture
def volunteer(db):
    return User.objects.create_user(email="volunteer@example.com", password="secret")


@pytest.fixture
def application(event, volunteer):
    return TeamMemberApplication.objects.create(
        event=event,
        user=volunteer,
        status=ApplicationStatus.ACCEPTED,
    )


@pytest.fixture
def role(event):
    return TeamRole.objects.create(event=event, name="Help Desk")


@pytest.fixture
def location(event):
    return ShiftLocation.objects.create(event=event, name="Main Hall")


@pytest.fixture
def active_shift(event, location):
    return Shift.objects.create(
        event=event,
        name="Morning",
        location=location,
        start_time=now() - timedelta(minutes=30),
        end_time=now() + timedelta(hours=2),
    )


@pytest.fixture
def assignment(active_shift, volunteer, role):
    ShiftRoleAssignment.objects.create(shift=active_shift, role=role, capacity=5)
    return ShiftAssignment.objects.create(
        shift=active_shift,
        team_member=volunteer,
        role=role,
    )


@pytest.fixture
def cfm(event):
    return CallForTeamMembers.objects.create(
        event=event,
        title="Join us",
        active=True,
        shift_schedule_published=True,
    )


def _make_checkin(event, voucher_id=None, attendee_email=None, order_email=None, checkin_dt=None):
    order = SimpleNamespace(email=order_email or "")
    position = SimpleNamespace(
        voucher_id=voucher_id,
        attendee_email=attendee_email or "",
        order=order,
    )
    checkin_list = SimpleNamespace(event=event)
    return SimpleNamespace(
        position=position,
        list=checkin_list,
        datetime=checkin_dt or now(),
        type="entry",
    )


class TestResolveVolunteerApplication:
    @pytest.mark.django_db
    def test_resolve_via_email(self, event, volunteer, application):
        checkin = _make_checkin(event, attendee_email=volunteer.email)
        result = resolve_volunteer_application(checkin)
        assert result == application

    @pytest.mark.django_db
    def test_resolve_via_order_email(self, event, volunteer, application):
        checkin = _make_checkin(event, order_email=volunteer.email)
        result = resolve_volunteer_application(checkin)
        assert result == application

    @pytest.mark.django_db
    def test_resolve_via_voucher(self, event, volunteer, application):
        with scopes_disabled():
            from eventyay.base.models import Voucher

            voucher = Voucher.objects.create(event=event, code="VOLUNTEER-001")
            MemberVoucher.objects.create(application=application, voucher=voucher)

        checkin = _make_checkin(event, voucher_id=voucher.pk)
        result = resolve_volunteer_application(checkin)
        assert result == application

    @pytest.mark.django_db
    def test_resolve_returns_none_for_unknown(self, event):
        checkin = _make_checkin(event, attendee_email="stranger@example.com")
        result = resolve_volunteer_application(checkin)
        assert result is None

    @pytest.mark.django_db
    def test_resolve_ignores_rejected_application(self, event, volunteer):
        TeamMemberApplication.objects.create(
            event=event,
            user=volunteer,
            status=ApplicationStatus.REJECTED,
        )
        checkin = _make_checkin(event, attendee_email=volunteer.email)
        result = resolve_volunteer_application(checkin)
        assert result is None

    @pytest.mark.django_db
    def test_resolve_case_insensitive_email(self, event, volunteer, application):
        checkin = _make_checkin(event, attendee_email=volunteer.email.upper())
        result = resolve_volunteer_application(checkin)
        assert result == application


class TestStampShiftStart:
    @pytest.mark.django_db
    def test_stamps_active_shift(self, event, volunteer, application, assignment):
        checkin_dt = now()
        stamp_shift_start(volunteer, event, checkin_dt)
        assignment.refresh_from_db()
        assert assignment.started_at == checkin_dt

    @pytest.mark.django_db
    def test_does_not_stamp_past_shift(self, event, volunteer, application, role):
        past_shift = Shift.objects.create(
            event=event,
            name="Yesterday",
            start_time=now() - timedelta(days=1, hours=3),
            end_time=now() - timedelta(days=1),
        )
        ShiftRoleAssignment.objects.create(shift=past_shift, role=role, capacity=5)
        past_assignment = ShiftAssignment.objects.create(
            shift=past_shift,
            team_member=volunteer,
            role=role,
        )
        stamp_shift_start(volunteer, event, now())
        past_assignment.refresh_from_db()
        assert past_assignment.started_at is None

    @pytest.mark.django_db
    def test_does_not_stamp_future_shift(self, event, volunteer, application, role):
        future_shift = Shift.objects.create(
            event=event,
            name="Later today",
            start_time=now() + timedelta(hours=1),
            end_time=now() + timedelta(hours=3),
        )
        ShiftRoleAssignment.objects.create(shift=future_shift, role=role, capacity=5)
        future_assignment = ShiftAssignment.objects.create(
            shift=future_shift,
            team_member=volunteer,
            role=role,
        )
        stamp_shift_start(volunteer, event, now())
        future_assignment.refresh_from_db()
        assert future_assignment.started_at is None

    @pytest.mark.django_db
    def test_does_not_overwrite_existing_start(self, event, volunteer, application, assignment):
        original_dt = now() - timedelta(hours=1)
        assignment.started_at = original_dt
        assignment.save(update_fields=["started_at"])

        stamp_shift_start(volunteer, event, now())
        assignment.refresh_from_db()
        assert assignment.started_at == original_dt


class TestHandleVolunteerCheckin:
    @pytest.mark.django_db
    @patch("teamshifts.services.checkin.maybe_auto_issue_certificate")
    def test_marks_arrived_and_stamps_shift(self, mock_cert, event, volunteer, application, assignment):
        checkin = _make_checkin(event, attendee_email=volunteer.email, checkin_dt=now())
        handle_volunteer_checkin(checkin)

        application.refresh_from_db()
        assert application.arrived is True

        assignment.refresh_from_db()
        assert assignment.started_at is not None

        mock_cert.assert_called_once_with(application)

    @pytest.mark.django_db
    @patch("teamshifts.services.checkin.maybe_auto_issue_certificate")
    def test_already_arrived_no_double_save(self, mock_cert, event, volunteer, application, assignment):
        application.arrived = True
        application.save(update_fields=["arrived"])

        checkin = _make_checkin(event, attendee_email=volunteer.email, checkin_dt=now())
        handle_volunteer_checkin(checkin)

        application.refresh_from_db()
        assert application.arrived is True
        mock_cert.assert_called_once()

    @pytest.mark.django_db
    @patch("teamshifts.services.checkin.maybe_auto_issue_certificate")
    def test_no_match_does_nothing(self, mock_cert, event):
        checkin = _make_checkin(event, attendee_email="nobody@example.com")
        handle_volunteer_checkin(checkin)
        mock_cert.assert_not_called()


class TestEndShift:
    @pytest.mark.django_db
    @patch("teamshifts.services.checkin.maybe_auto_issue_certificate")
    def test_end_shift_sets_ended_at(self, mock_cert, event, volunteer, application, assignment):
        assignment.started_at = now() - timedelta(hours=1)
        assignment.save(update_fields=["started_at"])

        end_dt = now()
        end_shift(assignment, end_dt)

        assignment.refresh_from_db()
        assert assignment.ended_at == end_dt
        mock_cert.assert_called_once_with(application)

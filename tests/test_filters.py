from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db.models import Count
from django.utils.timezone import now
from django_scopes import scope

from teamshifts.filters import (
    ApplicationFilterForm,
    MemberFilterForm,
    ShiftFilterForm,
    TeamShiftsEmailFilterForm,
    TeamShiftsSentEmailFilterForm,
)
from teamshifts.models import (
    ApplicationStatus,
    EmailTemplateRoles,
    Shift,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamMemberApplication,
    TeamRole,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
)


@pytest.fixture
def role_a(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Usher")


@pytest.fixture
def role_b(event):
    with scope(event=event):
        return TeamRole.objects.create(event=event, name="Security")


@pytest.fixture
def location_a(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Hall A")


@pytest.fixture
def location_b(event):
    with scope(event=event):
        return ShiftLocation.objects.create(event=event, name="Hall B")


@pytest.mark.django_db
def test_application_filter_query_and_privacy(event, django_user_model):
    with scope(event=event):
        user_alice = django_user_model.objects.create_user(email="alice@test.com", fullname="Alice Wonder")
        user_bob = django_user_model.objects.create_user(email="bob@secret.com", fullname="Bob Builder")
        app_a = TeamMemberApplication.objects.create(event=event, user=user_alice, status=ApplicationStatus.PENDING)
        TeamMemberApplication.objects.create(event=event, user=user_bob, status=ApplicationStatus.PENDING)
        qs = TeamMemberApplication.objects.filter(event=event)

        form = ApplicationFilterForm({"query": "Alice"}, event=event, hide_emails=False)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert results == [app_a]

        form = ApplicationFilterForm({"query": "secret.com"}, event=event, hide_emails=False)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert results == [TeamMemberApplication.objects.get(user=user_bob)]

        form = ApplicationFilterForm({"query": "secret.com"}, event=event, hide_emails=True)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert not results


@pytest.mark.django_db
def test_application_filter_status_and_no_role(event, django_user_model):
    with scope(event=event):
        user_alice = django_user_model.objects.create_user(email="alice@test.com", fullname="Alice")
        user_bob = django_user_model.objects.create_user(email="bob@test.com", fullname="Bob")
        app_a = TeamMemberApplication.objects.create(event=event, user=user_alice, status=ApplicationStatus.ACCEPTED)
        app_b = TeamMemberApplication.objects.create(event=event, user=user_bob, status=ApplicationStatus.PENDING)

        qs = TeamMemberApplication.objects.filter(event=event)

        form = ApplicationFilterForm({"status": ApplicationStatus.ACCEPTED}, event=event)
        assert "role" not in form.fields
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert results == [app_a]
        assert app_b not in results


@pytest.mark.django_db
def test_member_filter_arrived_and_vouchers(event, django_user_model, role_a, location_a):
    with scope(event=event):
        u1 = django_user_model.objects.create_user(email="m1@test.com", fullname="Member 1")
        u2 = django_user_model.objects.create_user(email="m2@test.com", fullname="Member 2")
        app1 = TeamMemberApplication.objects.create(event=event, user=u1, status=ApplicationStatus.ACCEPTED, arrived=True)
        app2 = TeamMemberApplication.objects.create(event=event, user=u2, status=ApplicationStatus.ACCEPTED, arrived=False)

        qs = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED)

        form = MemberFilterForm({"arrived": "yes"}, event=event, vouchers_enabled=False)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [app1]

        form = MemberFilterForm({"arrived": "no"}, event=event, vouchers_enabled=False)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [app2]

        form_no_vouchers = MemberFilterForm({}, event=event, vouchers_enabled=False)
        assert "voucher" not in form_no_vouchers.fields

        form_with_vouchers = MemberFilterForm({}, event=event, vouchers_enabled=True)
        assert "voucher" in form_with_vouchers.fields


@pytest.mark.django_db
def test_shift_filter_name_location_dates(event, location_a, location_b, role_a, role_b):
    with scope(event=event):
        base_time = now().replace(hour=10, minute=0, second=0, microsecond=0)
        s1 = Shift.objects.create(
            event=event,
            name="Alpha Shift",
            location=location_a,
            start_time=base_time,
            end_time=base_time + timedelta(hours=2),
        )
        s2 = Shift.objects.create(
            event=event,
            name="Beta Shift",
            location=location_b,
            start_time=base_time + timedelta(days=2),
            end_time=base_time + timedelta(days=2, hours=2),
        )
        ShiftRoleAssignment.objects.create(shift=s1, role=role_a, capacity=2)
        ShiftRoleAssignment.objects.create(shift=s2, role=role_b, capacity=2)

        qs = Shift.objects.filter(event=event)

        form = ShiftFilterForm({"query": "Alpha"}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [s1]

        form = ShiftFilterForm({"location": location_b.pk}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [s2]

        form = ShiftFilterForm({"role": role_a.pk}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [s1]

        start_date = base_time.date()
        form = ShiftFilterForm({"date_from": start_date, "date_to": start_date}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [s1]


@pytest.mark.django_db
def test_email_queue_filter(event, role_a, role_b, django_user_model):
    with scope(event=event):
        sender = django_user_model.objects.create_user(email="admin@test.com")
        q1 = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=sender,
            subject="Welcome Volunteers",
            message="Welcome",
            role_filter=role_a,
        )
        TeamShiftsEmailQueueRecipient.objects.create(queue=q1, email="volunteer1@test.com")

        q2 = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=sender,
            subject="Urgent Notice",
            message="Notice",
            role_filter=role_b,
        )
        TeamShiftsEmailQueueRecipient.objects.create(queue=q2, email="volunteer2@test.com")

        qs = TeamShiftsEmailQueue.objects.filter(event=event)

        form = TeamShiftsEmailFilterForm({"query": "Welcome"}, event=event, date_field="created")
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [q1]

        form = TeamShiftsEmailFilterForm({"query": "volunteer2@test.com"}, event=event, date_field="created")
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [q2]

        form = TeamShiftsEmailFilterForm({"role": role_a.pk}, event=event, date_field="created")
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [q1]

        form_hidden = TeamShiftsEmailFilterForm({"query": "volunteer2@test.com"}, event=event, date_field="created", hide_emails=True)
        assert form_hidden.is_valid()
        assert not list(form_hidden.filter_qs(qs))

        form_hidden_subject = TeamShiftsEmailFilterForm({"query": "Urgent"}, event=event, date_field="created", hide_emails=True)
        assert form_hidden_subject.is_valid()
        assert list(form_hidden_subject.filter_qs(qs)) == [q2]


@pytest.mark.django_db
def test_email_queue_filter_subquery_does_not_inflate_recipient_count(event, django_user_model):
    with scope(event=event):
        sender = django_user_model.objects.create_user(email="sender@test.com")
        queue = TeamShiftsEmailQueue.objects.create(event=event, user=sender, subject="Announcement", message="Hello")
        TeamShiftsEmailQueueRecipient.objects.create(queue=queue, email="recip1@test.com")
        TeamShiftsEmailQueueRecipient.objects.create(queue=queue, email="recip2@test.com")

        qs = TeamShiftsEmailQueue.objects.filter(event=event).annotate(recipient_count=Count("recipients"))
        form = TeamShiftsEmailFilterForm({"query": "Announcement"}, event=event, date_field="created")
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert len(results) == 1
        assert results[0].recipient_count == 2


@pytest.mark.django_db
def test_member_filter_role_subquery_does_not_inflate_shift_counts(event, django_user_model, role_a, location_a):
    with scope(event=event):
        member_user = django_user_model.objects.create_user(email="member@test.com", fullname="Member Multi")
        app = TeamMemberApplication.objects.create(event=event, user=member_user, status=ApplicationStatus.ACCEPTED)
        shift1 = Shift.objects.create(event=event, name="Shift 1", location=location_a, start_time=now(), end_time=now() + timedelta(hours=1))
        shift2 = Shift.objects.create(event=event, name="Shift 2", location=location_a, start_time=now(), end_time=now() + timedelta(hours=2))
        ShiftAssignment.objects.create(shift=shift1, team_member=member_user, role=role_a)
        ShiftAssignment.objects.create(shift=shift2, team_member=member_user, role=role_a)

        qs = TeamMemberApplication.objects.filter(event=event, status=ApplicationStatus.ACCEPTED).annotate(shifts_assigned=Count("user__shift_assignments"))
        form = MemberFilterForm({"role": role_a.pk}, event=event)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert len(results) == 1
        assert results[0] == app
        assert results[0].shifts_assigned == 2


@pytest.mark.django_db
def test_sent_email_filter(event, role_a, role_b, django_user_model):
    base_time = now()
    with scope(event=event):
        sender = django_user_model.objects.create_user(email="admin@test.com")
        alice = django_user_model.objects.create_user(email="alice@test.com", fullname="Alice Wonder")
        bob = django_user_model.objects.create_user(email="bob@test.com", fullname="Bob Builder")

        q1 = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=sender,
            subject="Voucher Subject",
            message="Body",
            role_filter=role_a,
            mail_type=EmailTemplateRoles.VOUCHER_SENT,
            sent_at=base_time,
        )
        r1 = TeamShiftsEmailQueueRecipient.objects.create(
            queue=q1,
            user=alice,
            email=alice.email,
            subject="Your voucher code",
            sent_at=base_time,
        )

        q2 = TeamShiftsEmailQueue.objects.create(
            event=event,
            user=sender,
            subject="Custom Broadcast",
            message="Body",
            role_filter=role_b,
            mail_type="",
            sent_at=base_time + timedelta(days=2),
        )
        r2 = TeamShiftsEmailQueueRecipient.objects.create(
            queue=q2,
            user=bob,
            email=bob.email,
            subject="",
            sent_at=base_time + timedelta(days=2),
        )

        qs = TeamShiftsEmailQueueRecipient.objects.filter(queue__event=event, sent_at__isnull=False)

        # 1. Search matching recipient subject
        form = TeamShiftsSentEmailFilterForm({"query": "voucher code"}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r1]

        # 2. Search fallback to queue subject when recipient subject is empty
        form = TeamShiftsSentEmailFilterForm({"query": "Broadcast"}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r2]

        # 3. Search matching recipient fullname
        form = TeamShiftsSentEmailFilterForm({"query": "Alice"}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r1]

        # 4. Search matching recipient email
        form = TeamShiftsSentEmailFilterForm({"query": "bob@test.com"}, event=event, hide_emails=False)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r2]

        # 5. Search respecting email privacy
        form = TeamShiftsSentEmailFilterForm({"query": "bob@test.com"}, event=event, hide_emails=True)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == []

        # 6. Legacy 'q' query parameter alias
        form = TeamShiftsSentEmailFilterForm({"q": "Alice"}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r1]

        # 7. Role filter
        form = TeamShiftsSentEmailFilterForm({"role": role_a.pk}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r1]

        # 8. Mail type filter: Custom
        form = TeamShiftsSentEmailFilterForm({"mail_type": "custom"}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r2]

        # 9. Mail type filter: Specific lifecycle type
        form = TeamShiftsSentEmailFilterForm({"mail_type": EmailTemplateRoles.VOUCHER_SENT}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r1]

        # 10. Date range filter
        start_date = base_time.date()
        form = TeamShiftsSentEmailFilterForm({"date_from": start_date, "date_to": start_date}, event=event)
        assert form.is_valid()
        assert list(form.filter_qs(qs)) == [r1]


@pytest.mark.django_db
def test_stable_default_ordering_with_shared_timestamps(event, django_user_model):
    with scope(event=event):
        shared_time = now()
        u1 = django_user_model.objects.create_user(email="user1@test.com", fullname="Same Name")
        u2 = django_user_model.objects.create_user(email="user2@test.com", fullname="Same Name")

        # Applications with identical created_at
        app1 = TeamMemberApplication.objects.create(event=event, user=u1, status=ApplicationStatus.PENDING)
        app2 = TeamMemberApplication.objects.create(event=event, user=u2, status=ApplicationStatus.PENDING)
        TeamMemberApplication.objects.filter(pk__in=[app1.pk, app2.pk]).update(created_at=shared_time)

        app_form = ApplicationFilterForm({}, event=event)
        assert app_form.is_valid()
        app_qs = app_form.filter_qs(TeamMemberApplication.objects.filter(event=event))
        # Descending: app2 (higher pk) comes before app1 (lower pk)
        assert list(app_qs) == [app2, app1]

        # Email queues with identical created timestamp
        q1 = TeamShiftsEmailQueue.objects.create(event=event, subject="A", created=shared_time, user=u1)
        q2 = TeamShiftsEmailQueue.objects.create(event=event, subject="B", created=shared_time, user=u1)
        email_form = TeamShiftsEmailFilterForm({}, event=event)
        assert email_form.is_valid()
        email_qs = email_form.filter_qs(TeamShiftsEmailQueue.objects.filter(event=event))
        assert list(email_qs) == [q2, q1]

        # Shifts with identical start_time
        s1 = Shift.objects.create(event=event, start_time=shared_time, end_time=shared_time + timedelta(hours=2))
        s2 = Shift.objects.create(event=event, start_time=shared_time, end_time=shared_time + timedelta(hours=2))
        shift_form = ShiftFilterForm({}, event=event)
        assert shift_form.is_valid()
        shift_qs = shift_form.filter_qs(Shift.objects.filter(event=event))
        assert list(shift_qs) == [s1, s2]


@pytest.mark.django_db
def test_shift_filter_staffing(event, django_user_model, location_a, role_a, role_b):
    with scope(event=event):
        volunteer = django_user_model.objects.create_user(email="vol@test.com", fullname="Vol")
        base_time = now()

        shift_no_roles = Shift.objects.create(
            event=event,
            name="Unconfigured Shift",
            location=location_a,
            start_time=base_time,
            end_time=base_time + timedelta(hours=2),
        )
        shift_open = Shift.objects.create(
            event=event,
            name="Open Shift",
            location=location_a,
            start_time=base_time + timedelta(hours=3),
            end_time=base_time + timedelta(hours=5),
        )
        shift_full = Shift.objects.create(
            event=event,
            name="Full Shift",
            location=location_a,
            start_time=base_time + timedelta(hours=6),
            end_time=base_time + timedelta(hours=8),
        )

        # shift_open has one filled role and one open role
        ShiftRoleAssignment.objects.create(shift=shift_open, role=role_a, capacity=1)
        ShiftRoleAssignment.objects.create(shift=shift_open, role=role_b, capacity=2)
        ShiftAssignment.objects.create(shift=shift_open, team_member=volunteer, role=role_a)

        # shift_full has all roles at capacity
        ShiftRoleAssignment.objects.create(shift=shift_full, role=role_a, capacity=1)
        ShiftAssignment.objects.create(shift=shift_full, team_member=volunteer, role=role_a)

        qs = Shift.objects.filter(event=event)

        form_open = ShiftFilterForm({"staffing": "open"}, event=event)
        assert form_open.is_valid()
        assert list(form_open.filter_qs(qs)) == [shift_open]

        form_full = ShiftFilterForm({"staffing": "full"}, event=event)
        assert form_full.is_valid()
        assert list(form_full.filter_qs(qs)) == [shift_full]
        assert shift_no_roles not in list(form_open.filter_qs(qs))
        assert shift_no_roles not in list(form_full.filter_qs(qs))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "form_cls",
    [ShiftFilterForm, TeamShiftsEmailFilterForm, TeamShiftsSentEmailFilterForm],
)
def test_reversed_date_range_is_invalid(event, form_cls):
    with scope(event=event):
        form = form_cls({"date_from": "2026-06-15", "date_to": "2026-06-10"}, event=event)
        assert not form.is_valid()
        assert "date_to" in form.errors

        equal_form = form_cls({"date_from": "2026-06-15", "date_to": "2026-06-15"}, event=event)
        assert equal_form.is_valid()


@pytest.mark.django_db
def test_date_to_boundary_in_non_utc_event_timezone(event, django_user_model):
    from eventyay.base.models import Event

    ny_tz = ZoneInfo("America/New_York")
    ist_tz = ZoneInfo("Asia/Kolkata")

    # UTC-negative timezone: 23:30 EDT on June 15 is already 03:30 UTC on June 16
    Event.objects.filter(pk=event.pk).update(timezone="America/New_York")
    ny_event = Event.objects.get(pk=event.pk)
    with scope(event=ny_event):
        ny_loc = ShiftLocation.objects.create(event=ny_event, name="NY Stage")
        inside_ny = Shift.objects.create(
            event=ny_event,
            name="Late NY Shift",
            location=ny_loc,
            start_time=datetime(2026, 6, 15, 23, 30, tzinfo=ny_tz),
            end_time=datetime(2026, 6, 16, 1, 30, tzinfo=ny_tz),
        )
        outside_ny = Shift.objects.create(
            event=ny_event,
            name="Next Day NY Shift",
            location=ny_loc,
            start_time=datetime(2026, 6, 16, 0, 5, tzinfo=ny_tz),
            end_time=datetime(2026, 6, 16, 2, 5, tzinfo=ny_tz),
        )
        form = ShiftFilterForm({"date_to": "2026-06-15"}, event=ny_event)
        assert form.is_valid()
        ny_results = list(form.filter_qs(Shift.objects.filter(event=ny_event)))
        assert ny_results == [inside_ny]
        assert outside_ny not in ny_results

    # UTC-positive timezone: 01:00 IST on June 16 is still 19:30 UTC on June 15
    Event.objects.filter(pk=event.pk).update(timezone="Asia/Kolkata")
    ist_event = Event.objects.get(pk=event.pk)
    with scope(event=ist_event):
        Shift.objects.filter(event=ist_event).delete()
        inside_ist = Shift.objects.create(
            event=ist_event,
            name="Late IST Shift",
            location=ny_loc,
            start_time=datetime(2026, 6, 15, 23, 30, tzinfo=ist_tz),
            end_time=datetime(2026, 6, 16, 0, 30, tzinfo=ist_tz),
        )
        outside_ist = Shift.objects.create(
            event=ist_event,
            name="Next Day IST Shift",
            location=ny_loc,
            start_time=datetime(2026, 6, 16, 1, 0, tzinfo=ist_tz),
            end_time=datetime(2026, 6, 16, 2, 0, tzinfo=ist_tz),
        )
        form = ShiftFilterForm({"date_to": "2026-06-15"}, event=ist_event)
        assert form.is_valid()
        results = list(form.filter_qs(Shift.objects.filter(event=ist_event)))
        assert results == [inside_ist]
        assert outside_ist not in results

        # Sent email filter also respects non-UTC event timezone on date_to
        sender = django_user_model.objects.create_user(email="tzsender@test.com")
        q_in = TeamShiftsEmailQueue.objects.create(event=ist_event, user=sender, subject="In IST", message="Body")
        r_in = TeamShiftsEmailQueueRecipient.objects.create(
            queue=q_in,
            email="r1@test.com",
            sent_at=datetime(2026, 6, 15, 23, 59, tzinfo=ist_tz),
        )
        q_out = TeamShiftsEmailQueue.objects.create(event=ist_event, user=sender, subject="Out IST", message="Body")
        TeamShiftsEmailQueueRecipient.objects.create(
            queue=q_out,
            email="r2@test.com",
            sent_at=datetime(2026, 6, 16, 0, 30, tzinfo=ist_tz),
        )
        sent_form = TeamShiftsSentEmailFilterForm({"date_to": "2026-06-15"}, event=ist_event)
        assert sent_form.is_valid()
        sent_results = list(sent_form.filter_qs(TeamShiftsEmailQueueRecipient.objects.filter(queue__event=ist_event)))
        assert sent_results == [r_in]

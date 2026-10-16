from datetime import timedelta

import pytest
from django.utils.timezone import now
from django_scopes import scope

from teamshifts.filters import (
    ApplicationFilterForm,
    MemberFilterForm,
    ShiftFilterForm,
    TeamShiftsEmailFilterForm,
)
from teamshifts.models import (
    ApplicationStatus,
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
def test_application_filter_status_and_role(event, django_user_model, role_a, role_b, location_a):
    with scope(event=event):
        user_alice = django_user_model.objects.create_user(email="alice@test.com", fullname="Alice")
        user_bob = django_user_model.objects.create_user(email="bob@test.com", fullname="Bob")
        app_a = TeamMemberApplication.objects.create(event=event, user=user_alice, status=ApplicationStatus.ACCEPTED)
        app_b = TeamMemberApplication.objects.create(event=event, user=user_bob, status=ApplicationStatus.PENDING)

        shift = Shift.objects.create(
            event=event,
            name="Morning Shift",
            location=location_a,
            start_time=now(),
            end_time=now() + timedelta(hours=2),
        )
        ShiftAssignment.objects.create(shift=shift, team_member=user_alice, role=role_a)

        qs = TeamMemberApplication.objects.filter(event=event)

        form = ApplicationFilterForm({"status": ApplicationStatus.ACCEPTED}, event=event)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert results == [app_a]
        assert app_b not in results

        form = ApplicationFilterForm({"role": role_a.pk}, event=event)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert results == [app_a]

        form = ApplicationFilterForm({"role": role_b.pk}, event=event)
        assert form.is_valid()
        results = list(form.filter_qs(qs))
        assert not results


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

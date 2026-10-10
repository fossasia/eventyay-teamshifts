from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import Team, User, Voucher

from teamshifts.forms import TeamMemberApplicationForm
from teamshifts.models import (
    ApplicationStatus,
    CallForTeamMembers,
    MemberVoucher,
    Shift,
    ShiftAssignment,
    TeamMemberApplication,
    TeamRole,
    VolunteerVoucherSettings,
    VoucherStatus,
)
from teamshifts.services.members import AlreadyMemberError, add_member_from_organizer, resolve_or_create_user


@pytest.fixture
def call_for_team_members(event):
    with scope(event=event):
        return CallForTeamMembers.objects.create(event=event, active=True)


@pytest.fixture
def orga_user(event, user):
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Test Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    return user


@pytest.mark.django_db
def test_organizer_add_member_form_email_is_editable(event, call_for_team_members):
    form = TeamMemberApplicationForm(event=event, cfm=call_for_team_members, organizer_mode=True)
    assert "email" in form.fields
    assert form.fields["email"].widget.attrs.get("readonly") is None
    assert form.fields["email"].required is True


@pytest.mark.django_db
def test_add_member_creates_accepted_application(event, call_for_team_members):
    form = TeamMemberApplicationForm(
        data={
            "full_name": "Jane Member",
            "email": "jane.member@example.com",
            "phone_0": "+1",
            "phone_1": "201 555 0100",
            "availability_notes": "Weekends",
        },
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid(), form.errors
    application = add_member_from_organizer(event=event, form=form)
    assert application.status == ApplicationStatus.ACCEPTED
    assert application.added_by_organizer is True
    assert application.user.email == "jane.member@example.com"
    assert application.user.fullname == "Jane Member"
    assert application.phone == "+12015550100"


@pytest.mark.django_db
def test_resolve_or_create_user_keeps_existing_fullname(django_user_model):
    existing = django_user_model.objects.create_user(email="keep-name@example.com", password="x", fullname="Original Name")

    user, created = resolve_or_create_user(email="keep-name@example.com", full_name="Organizer Typo")

    assert created is False
    assert user.pk == existing.pk
    assert user.fullname == "Original Name"


@pytest.mark.django_db
def test_resolve_or_create_user_sets_fullname_when_blank(django_user_model):
    existing = django_user_model.objects.create_user(email="blank-name@example.com", password="x", fullname="")

    user, created = resolve_or_create_user(email="blank-name@example.com", full_name="New Name")

    assert created is False
    assert user.pk == existing.pk
    assert user.fullname == "New Name"


@pytest.mark.django_db
def test_add_member_rejects_existing_accepted(event, call_for_team_members, django_user_model):
    member = django_user_model.objects.create_user(email="existing@example.com", password="x", fullname="Existing")
    with scope(event=event):
        TeamMemberApplication.objects.create(event=event, user=member, status=ApplicationStatus.ACCEPTED)

    form = TeamMemberApplicationForm(
        data={"full_name": "Existing", "email": "existing@example.com"},
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid(), form.errors
    with pytest.raises(AlreadyMemberError):
        add_member_from_organizer(event=event, form=form)


@pytest.mark.django_db
def test_add_member_accepts_pending_application(event, call_for_team_members, django_user_model):
    member = django_user_model.objects.create_user(email="pending@example.com", password="x", fullname="Pending")
    with scope(event=event):
        application = TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.PENDING,
            phone="",
        )

    form = TeamMemberApplicationForm(
        data={"full_name": "Pending Updated", "email": "pending@example.com", "phone_0": "+1", "phone_1": "201 555 0123"},
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid(), form.errors
    updated = add_member_from_organizer(event=event, form=form)
    assert updated.pk == application.pk
    assert updated.status == ApplicationStatus.ACCEPTED
    assert updated.added_by_organizer is True
    assert updated.phone == "+12015550123"


@pytest.mark.django_db
@patch("teamshifts.views.queue_lifecycle_email")
def test_member_add_view_creates_member(mock_queue, client, event, call_for_team_members, orga_user, settings):
    settings.SITE_URL = "https://testserver"
    client.force_login(orga_user)
    url = reverse("plugins:teamshifts:member_add", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.post(
        url,
        {
            "full_name": "Org Added",
            "email": "org.added@example.com",
        },
    )
    assert response.status_code == 302
    with scope(event=event):
        application = TeamMemberApplication.objects.get(event=event, user__email="org.added@example.com")
        assert application.status == ApplicationStatus.ACCEPTED
        assert application.added_by_organizer is True
    assert User.objects.filter(email="org.added@example.com").exists()


@pytest.mark.django_db
def test_organizer_added_member_excluded_from_applicants_list(client, event, call_for_team_members, orga_user, django_user_model, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        applicant = django_user_model.objects.create_user(email="applicant@example.com", password="x", fullname="Applicant")
        TeamMemberApplication.objects.create(event=event, user=applicant, status=ApplicationStatus.PENDING)

    form = TeamMemberApplicationForm(
        data={"full_name": "Org Added", "email": "orgmember@example.com"},
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid(), form.errors
    add_member_from_organizer(event=event, form=form)

    client.force_login(orga_user)
    url = reverse("plugins:teamshifts:applications", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.get(url)
    assert response.status_code == 200
    emails = {app.user.email for app in response.context["applications"]}
    assert "applicant@example.com" in emails
    assert "orgmember@example.com" not in emails


@pytest.mark.django_db
def test_organizer_added_member_appears_on_members_list(client, event, call_for_team_members, orga_user, settings):
    settings.SITE_URL = "https://testserver"
    form = TeamMemberApplicationForm(
        data={"full_name": "Org Added", "email": "orgmember2@example.com"},
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid(), form.errors
    add_member_from_organizer(event=event, form=form)

    client.force_login(orga_user)
    url = reverse("plugins:teamshifts:members", kwargs={"organizer": event.organizer.slug, "event": event.slug})
    response = client.get(url)
    assert response.status_code == 200
    emails = {member.user.email for member in response.context["members"]}
    assert "orgmember2@example.com" in emails


# Phone numbers are validated with `phonenumbers` against the rules of the selected country,
# not against a fixed 7–15 digit range.
@pytest.mark.django_db
def test_phone_validation_rejects_too_long_for_country(event, call_for_team_members):
    form = TeamMemberApplicationForm(
        data={
            "full_name": "Jane Member",
            "email": "jane@example.com",
            "phone_0": "+1",
            "phone_1": "123 456 7890 12345678",
        },
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert not form.is_valid()
    assert "phone" in form.errors


@pytest.mark.django_db
def test_phone_validation_rejects_too_short_for_country(event, call_for_team_members):
    form = TeamMemberApplicationForm(
        data={
            "full_name": "Jane Member",
            "email": "jane@example.com",
            "phone_0": "+1",
            "phone_1": "123",
        },
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert not form.is_valid()
    assert "phone" in form.errors


@pytest.mark.django_db
@pytest.mark.parametrize("bad_phone", ["abc 123 xyz", "123(456)7890", "123(---)4567890"])
def test_phone_validation_rejects_garbage(event, call_for_team_members, bad_phone):
    form = TeamMemberApplicationForm(
        data={
            "full_name": "Jane Member",
            "email": "jane@example.com",
            "phone_0": "+1",
            "phone_1": bad_phone,
        },
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert not form.is_valid()
    assert "phone" in form.errors


@pytest.mark.django_db
def test_phone_validation_accepts_valid(event, call_for_team_members):
    form = TeamMemberApplicationForm(
        data={
            "full_name": "Jane Member",
            "email": "jane@example.com",
            "phone_0": "+1",
            "phone_1": "201-555-0123",
        },
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("prefix", "number", "valid"),
    [
        ("+1", "201 555 0123", True),
        ("+91", "201 555 0123", False),
        ("+91", "98765 43210", True),
        ("+1", "98765 43210", False),
    ],
)
def test_phone_validation_depends_on_selected_country(event, call_for_team_members, prefix, number, valid):
    form = TeamMemberApplicationForm(
        data={"full_name": "Jane Member", "email": "jane@example.com", "phone_0": prefix, "phone_1": number},
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert form.is_valid() is valid, form.errors


INVALID_LEGACY_PHONE_VALUES = ["555.123.4567", "call me after 6pm"]
LEGACY_PHONE_VALUES = [*INVALID_LEGACY_PHONE_VALUES, "+1 (201) 555-0123 ext. 4"]


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_phone", LEGACY_PHONE_VALUES)
def test_legacy_phone_value_renders_unchanged_in_form(event, call_for_team_members, legacy_phone):
    form = TeamMemberApplicationForm(initial={"phone": legacy_phone}, event=event, cfm=call_for_team_members, organizer_mode=True)
    html = str(form["phone"])
    assert f'value="{legacy_phone}"' in html


@pytest.mark.django_db
def test_valid_legacy_phone_value_is_split_into_prefix_and_number(event, call_for_team_members):
    form = TeamMemberApplicationForm(initial={"phone": "+1 201-555-0123"}, event=event, cfm=call_for_team_members, organizer_mode=True)
    assert form.fields["phone"].prepare_value("+1 201-555-0123") == "+12015550123"
    html = str(form["phone"])
    assert 'value="2015550123"' in html


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_phone", INVALID_LEGACY_PHONE_VALUES)
def test_legacy_phone_value_resubmitted_is_rejected_with_form_error(event, call_for_team_members, legacy_phone):
    form = TeamMemberApplicationForm(
        data={"full_name": "Jane Member", "email": "jane@example.com", "phone_0": "", "phone_1": legacy_phone},
        event=event,
        cfm=call_for_team_members,
        organizer_mode=True,
    )
    assert not form.is_valid()
    assert "phone" in form.errors
    assert f'value="{legacy_phone}"' in str(form["phone"])


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_phone", LEGACY_PHONE_VALUES)
def test_saved_legacy_phone_still_renders_in_organizer_views(client, event, call_for_team_members, orga_user, django_user_model, settings, legacy_phone):
    settings.SITE_URL = "https://testserver"
    applicant = django_user_model.objects.create_user(email="legacy@example.com", password="x", fullname="Legacy Applicant")
    with scope(event=event):
        application = TeamMemberApplication.objects.create(event=event, user=applicant, status=ApplicationStatus.PENDING, phone=legacy_phone)

    client.force_login(orga_user)
    kwargs = {"organizer": event.organizer.slug, "event": event.slug}
    list_response = client.get(reverse("plugins:teamshifts:applications", kwargs=kwargs))
    detail_response = client.get(reverse("plugins:teamshifts:application_detail", kwargs={**kwargs, "pk": application.pk}))

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert legacy_phone in detail_response.content.decode()


@pytest.mark.django_db
def test_members_filters(client, event, orga_user, django_user_model, call_for_team_members, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        members = []

        for i, email in enumerate(["none@example.com", "sent@example.com", "claimed@example.com"]):
            user = django_user_model.objects.create_user(
                email=email,
                password="x",
                fullname=f"Member {i}",
            )
            app = TeamMemberApplication.objects.create(
                event=event,
                user=user,
                status=ApplicationStatus.ACCEPTED,
            )
            members.append(app)

        stale_user = django_user_model.objects.create_user(
            email="stale@example.com",
            password="x",
            fullname="Stale Member",
        )
        stale_application = TeamMemberApplication.objects.create(
            event=event,
            user=stale_user,
            status=ApplicationStatus.ACCEPTED,
        )

        sent_voucher = Voucher.objects.create(
            event=event,
            tag="test",
            max_usages=1,
            redeemed=0,
        )
        MemberVoucher.objects.create(
            application=members[1],
            voucher=sent_voucher,
            status=VoucherStatus.SENT,
        )

        claimed_voucher = Voucher.objects.create(
            event=event,
            tag="test",
            max_usages=1,
            redeemed=1,
        )
        MemberVoucher.objects.create(
            application=members[2],
            voucher=claimed_voucher,
            status=VoucherStatus.CLAIMED,
        )

        stale_sent_voucher = Voucher.objects.create(
            event=event,
            tag="stale",
            max_usages=1,
            redeemed=1,
        )
        MemberVoucher.objects.create(
            application=stale_application,
            voucher=stale_sent_voucher,
            status=VoucherStatus.SENT,
        )

    client.force_login(orga_user)

    url = reverse(
        "plugins:teamshifts:members",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.get(url, {"voucher": "not_sent"})
    assert response.status_code == 200
    assert list(response.context["members"].values_list("user__email", flat=True)) == ["none@example.com"]

    response = client.get(url, {"voucher": "sent"})
    assert list(response.context["members"].values_list("user__email", flat=True)) == ["sent@example.com"]

    response = client.get(url, {"voucher": "claimed"})
    assert set(response.context["members"].values_list("user__email", flat=True)) == {
        "claimed@example.com",
        "stale@example.com",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "sort, expected_first",
    [
        ("display_name_sort", "Alice"),
        ("-display_name_sort", "Bob"),
        ("user__email", "alice@example.com"),
        ("-user__email", "bob@example.com"),
        ("role_sort", "Bob"),
        ("-role_sort", "Alice"),
        ("hours_scheduled", "Bob"),
        ("-hours_scheduled", "Alice"),
        ("voucher_sort", "Alice"),
        ("-voucher_sort", "Bob"),
        ("arrived", "Bob"),
        ("-arrived", "Alice"),
    ],
)
def test_members_sorting(client, event, orga_user, django_user_model, sort, expected_first, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        alice = django_user_model.objects.create_user(
            email="alice@example.com",
            password="x",
            fullname="Bob",
        )
        bob = django_user_model.objects.create_user(
            email="bob@example.com",
            password="x",
            fullname="Alice",
        )

        alice_application = TeamMemberApplication.objects.create(
            event=event,
            user=alice,
            status=ApplicationStatus.ACCEPTED,
            arrived=False,
        )
        TeamMemberApplication.objects.create(
            event=event,
            user=bob,
            status=ApplicationStatus.ACCEPTED,
            arrived=True,
        )

        role_a = TeamRole.objects.create(event=event, name="A Role")
        role_b = TeamRole.objects.create(event=event, name="B Role")

        shift_a = Shift.objects.create(
            event=event,
            name="Shift A",
            start_time=now(),
            end_time=now() + timedelta(hours=1),
        )
        shift_b = Shift.objects.create(
            event=event,
            name="Shift B",
            start_time=now() + timedelta(hours=2),
            end_time=now() + timedelta(hours=4),
        )

        ShiftAssignment.objects.create(
            shift=shift_a,
            team_member=alice,
            role=role_a,
        )
        ShiftAssignment.objects.create(
            shift=shift_b,
            team_member=bob,
            role=role_b,
        )

        voucher = Voucher.objects.create(
            event=event,
            tag="test",
            max_usages=1,
            redeemed=0,
        )
        MemberVoucher.objects.create(
            application=alice_application,
            voucher=voucher,
            status=VoucherStatus.SENT,
        )

    client.force_login(orga_user)

    url = reverse(
        "plugins:teamshifts:members",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.get(url, {"sort": sort})
    assert response.status_code == 200
    member = response.context["members"][0].user
    actual_first = member.email if "email" in sort else member.fullname

    assert actual_first == expected_first


@pytest.mark.django_db
def test_bulk_vouchers_preserves_filters_and_sort(client, event, orga_user, django_user_model):
    with scope(event=event):
        user = django_user_model.objects.create_user(
            email="voucher@example.com",
            password="x",
            fullname="Voucher Member",
        )
        application = TeamMemberApplication.objects.create(
            event=event,
            user=user,
            status=ApplicationStatus.ACCEPTED,
        )
        VolunteerVoucherSettings.objects.create(
            event=event,
            enabled=True,
            voucher_tag="test-vouchers",
        )

    client.force_login(orga_user)

    url = reverse(
        "plugins:teamshifts:bulk_send_vouchers",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
        },
    )

    response = client.post(
        url + "?q=Voucher&hours=has&voucher=not_sent&sort=-display_name_sort",
        {"member_ids": [application.pk]},
    )

    assert response.status_code == 302
    assert "q=Voucher" in response.url
    assert "hours=has" in response.url
    assert "voucher=not_sent" in response.url
    assert "sort=-display_name_sort" in response.url


@pytest.mark.django_db
def test_member_arrived_toggle_with_members_filters_and_sort(client, event, orga_user, django_user_model, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        member = django_user_model.objects.create_user(
            email="arrived@example.com",
            password="x",
            fullname="Arrived Member",
        )
        application = TeamMemberApplication.objects.create(
            event=event,
            user=member,
            status=ApplicationStatus.ACCEPTED,
            arrived=False,
        )
        Team.objects.create(
            organizer=event.organizer,
            name="Lead Team",
            teamshifts_role="lead",
            all_events=True,
        ).members.add(orga_user)

    client.force_login(orga_user)

    members_url = reverse(
        "plugins:teamshifts:members",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    toggle_url = reverse(
        "plugins:teamshifts:member_toggle_arrived",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "pk": application.pk,
        },
    )
    query = "?q=Arrived&voucher=not_sent&sort=-display_name_sort"

    response = client.post(
        toggle_url,
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "arrived": True}

    application.refresh_from_db()
    assert application.arrived is True

    list_response = client.get(members_url + query)

    assert list_response.status_code == 200
    assert list(list_response.context["members"].values_list("user__fullname", flat=True)) == ["Arrived Member"]

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled
from eventyay.base.models import Event, Team, Voucher

from teamshifts.forms import EmailComposeForm
from teamshifts.models import (
    ApplicationStatus,
    MemberVoucher,
    RecipientGroup,
    Shift,
    ShiftAssignment,
    TeamMemberApplication,
    TeamShiftsEmailQueue,
    TeamShiftsEmailQueueRecipient,
    VolunteerVoucherSettings,
    VoucherStatus,
)
from teamshifts.services.email import get_recipients, queue_email
from teamshifts.tasks import send_queued_email

VOUCHER_TAG = "volunteer-batch"


@pytest.fixture
def orga_client(client, event, user, settings):
    settings.SITE_URL = "https://testserver"
    with scope(event=event):
        team = Team.objects.create(
            organizer=event.organizer,
            name="Orga Team",
            can_change_event_settings=True,
            all_events=True,
        )
        team.members.add(user)
    client.force_login(user)
    return client


@pytest.fixture
def vouchers_enabled(event):
    with scopes_disabled():
        return VolunteerVoucherSettings.objects.create(event=event, enabled=True, voucher_tag=VOUCHER_TAG)


def _url(event, name, **kwargs):
    return reverse(
        f"plugins:teamshifts:{name}",
        kwargs={"organizer": event.organizer.slug, "event": event.slug, **kwargs},
    )


def _member(event, django_user_model, email, status=ApplicationStatus.ACCEPTED):
    user = django_user_model.objects.create_user(email=email, password="x", fullname=email.split("@")[0], locale="en")
    with scope(event=event):
        return TeamMemberApplication.objects.create(event=event, user=user, status=status)


def _assign_shift(event, user):
    with scope(event=event):
        shift = Shift.objects.create(event=event, start_time=now(), end_time=now() + timedelta(hours=2))
        ShiftAssignment.objects.create(shift=shift, team_member=user)


def _give_voucher(event, application, *, status=VoucherStatus.SENT, redeemed=0):
    with scopes_disabled():
        voucher = Voucher.objects.create(event=event, tag=VOUCHER_TAG, max_usages=1, redeemed=redeemed)
        return MemberVoucher.objects.create(
            event=event,
            application=application,
            voucher=voucher,
            status=status,
            sent_at=None if status == VoucherStatus.NOT_SENT else now(),
        )


def _emails(users):
    return {u.email for u in users}


def _send_to_choices(form):
    return [value for value, _label in form.fields["status"].choices]


@pytest.mark.django_db
def test_without_shifts_group_has_only_accepted_members_without_a_shift_in_this_event(event, django_user_model):
    free = _member(event, django_user_model, "free@example.com")
    busy = _member(event, django_user_model, "busy@example.com")
    elsewhere = _member(event, django_user_model, "elsewhere@example.com")
    _member(event, django_user_model, "pending@example.com", status=ApplicationStatus.PENDING)
    other_event = Event.objects.create(organizer=event.organizer, name="Other", slug="other", date_from=now(), plugins="teamshifts")
    _assign_shift(event, busy.user)
    _assign_shift(other_event, elsewhere.user)

    recipients = get_recipients(event, group=RecipientGroup.WITHOUT_SHIFTS)

    assert _emails(recipients) == {free.user.email, elsewhere.user.email}


@pytest.mark.django_db
def test_voucher_not_used_group_has_only_members_with_a_sent_unredeemed_voucher(event, django_user_model):
    unused = _member(event, django_user_model, "unused@example.com")
    _give_voucher(event, unused)
    _give_voucher(event, _member(event, django_user_model, "claimed@example.com"), status=VoucherStatus.CLAIMED, redeemed=1)
    _give_voucher(event, _member(event, django_user_model, "redeemed@example.com"), redeemed=1)
    _give_voucher(event, _member(event, django_user_model, "failed@example.com"), status=VoucherStatus.NOT_SENT)
    _give_voucher(event, _member(event, django_user_model, "rejected@example.com", status=ApplicationStatus.REJECTED))
    _member(event, django_user_model, "never@example.com")

    recipients = get_recipients(event, group=RecipientGroup.VOUCHER_NOT_USED)

    assert _emails(recipients) == {unused.user.email}


@pytest.mark.django_db
def test_send_to_dropdown_lists_the_groups_in_order_with_their_size(event, django_user_model, vouchers_enabled):
    _member(event, django_user_model, "free@example.com")
    _give_voucher(event, _member(event, django_user_model, "unused@example.com"))

    form = EmailComposeForm(event=event)
    field = form.fields["status"]

    assert str(field.label) == "Send to"
    assert field.initial == ApplicationStatus.ACCEPTED
    assert _send_to_choices(form) == [
        "",
        ApplicationStatus.ACCEPTED,
        RecipientGroup.WITHOUT_SHIFTS,
        RecipientGroup.VOUCHER_NOT_USED,
        ApplicationStatus.PENDING,
        ApplicationStatus.REJECTED,
    ]
    labels = dict(field.choices)
    assert str(labels[""]) == "Everyone (all statuses)"
    assert labels[RecipientGroup.WITHOUT_SHIFTS] == "Accepted – without shifts (2)"
    assert labels[RecipientGroup.VOUCHER_NOT_USED] == "Accepted – voucher not used (1)"


@pytest.mark.django_db
@pytest.mark.parametrize("voucher_settings", [None, {"enabled": False, "voucher_tag": VOUCHER_TAG}, {"enabled": True, "voucher_tag": ""}])
def test_voucher_group_is_hidden_unless_vouchers_are_enabled(event, voucher_settings):
    if voucher_settings is not None:
        with scopes_disabled():
            VolunteerVoucherSettings.objects.create(event=event, **voucher_settings)

    choices = _send_to_choices(EmailComposeForm(event=event))

    assert RecipientGroup.WITHOUT_SHIFTS in choices
    assert RecipientGroup.VOUCHER_NOT_USED not in choices


@pytest.mark.django_db
def test_voucher_group_cannot_be_sent_while_vouchers_are_disabled(orga_client, event, django_user_model):
    _give_voucher(event, _member(event, django_user_model, "unused@example.com"))

    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        response = orga_client.post(
            _url(event, "email_compose"),
            {"action": "send", "status": RecipientGroup.VOUCHER_NOT_USED, "subject_0": "s", "message_0": "m", "send_after": ""},
        )

    assert response.status_code == 200
    assert "status" in response.context["form"].errors
    mock_dispatch.assert_not_called()
    with scope(event=event):
        assert not TeamShiftsEmailQueue.objects.filter(event=event).exists()


@pytest.mark.django_db
def test_preview_lists_exactly_the_people_in_the_group(orga_client, event, django_user_model):
    free = _member(event, django_user_model, "free@example.com")
    busy = _member(event, django_user_model, "busy@example.com")
    _assign_shift(event, busy.user)

    response = orga_client.post(_url(event, "email_compose"), {"action": "preview", "status": RecipientGroup.WITHOUT_SHIFTS})

    assert response.status_code == 200
    assert _emails(response.context["preview_recipients"]) == {free.user.email}
    assert f'value="{RecipientGroup.WITHOUT_SHIFTS}" selected' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(("action", "scheduled"), [("send", False), ("send", True), ("outbox", False), ("draft", False)])
def test_compose_stores_the_group_for_every_way_of_sending(orga_client, event, django_user_model, action, scheduled):
    free = _member(event, django_user_model, "free@example.com")
    _assign_shift(event, _member(event, django_user_model, "busy@example.com").user)
    send_after = f"{now() + timedelta(days=1):%Y-%m-%dT%H:%M}" if scheduled else ""

    with patch("teamshifts.services.email._dispatch") as mock_dispatch:
        response = orga_client.post(
            _url(event, "email_compose"),
            {"action": action, "status": RecipientGroup.WITHOUT_SHIFTS, "subject_0": "Pick shifts", "message_0": "m", "send_after": send_after},
        )

    assert response.status_code == 302
    assert mock_dispatch.called is (action == "send")
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.get(event=event)
        emails = set(queue.recipients.values_list("email", flat=True))
    assert queue.recipient_group == RecipientGroup.WITHOUT_SHIFTS
    assert queue.status_filter == ApplicationStatus.ACCEPTED
    assert queue.is_draft is (action == "draft")
    assert (queue.send_after is not None) is scheduled
    assert emails == {free.user.email}


@pytest.mark.django_db
@pytest.mark.parametrize("group", RecipientGroup.values)
def test_reopening_a_draft_keeps_the_group(orga_client, event, user, vouchers_enabled, group):
    with scope(event=event):
        draft = queue_email(event, "s", "m", [], user=user, status_filter=ApplicationStatus.ACCEPTED, recipient_group=group, is_draft=True)

    content = orga_client.get(_url(event, "email_compose") + f"?draft={draft.pk}").content.decode()

    assert f'value="{group}" selected' in content


@pytest.mark.django_db
def test_reopening_a_voucher_draft_keeps_the_group_after_vouchers_are_disabled(orga_client, event, user):
    with scope(event=event):
        draft = queue_email(
            event, "s", "m", [], user=user, status_filter=ApplicationStatus.ACCEPTED, recipient_group=RecipientGroup.VOUCHER_NOT_USED, is_draft=True
        )

    content = orga_client.get(_url(event, "email_compose") + f"?draft={draft.pk}").content.decode()

    assert f'value="{RecipientGroup.VOUCHER_NOT_USED}" selected' in content


def _queue_for_group(event, group, send_after=None):
    with scope(event=event):
        return queue_email(
            event,
            "Reminder",
            "Body",
            get_recipients(event, group=group),
            status_filter=ApplicationStatus.ACCEPTED,
            recipient_group=group,
            send_after=send_after,
            dispatch=False,
        )


@pytest.mark.django_db
def test_scheduled_email_skips_members_who_signed_up_for_a_shift_in_the_meantime(event, django_user_model):
    stays = _member(event, django_user_model, "stays@example.com")
    signs_up = _member(event, django_user_model, "signs-up@example.com")
    queue = _queue_for_group(event, RecipientGroup.WITHOUT_SHIFTS, send_after=now() - timedelta(minutes=1))
    _assign_shift(event, signs_up.user)

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    assert [call.kwargs["email"] for call in mock_mail.call_args_list] == [stays.user.email]
    with scope(event=event):
        queue.refresh_from_db()
        assert set(queue.recipients.values_list("email", flat=True)) == {stays.user.email}
    assert queue.sent_at is not None


@pytest.mark.django_db
def test_outbox_email_skips_members_who_used_their_voucher_in_the_meantime(event, django_user_model):
    stays = _member(event, django_user_model, "stays@example.com")
    _give_voucher(event, stays)
    redeems = _member(event, django_user_model, "redeems@example.com")
    member_voucher = _give_voucher(event, redeems)
    queue = _queue_for_group(event, RecipientGroup.VOUCHER_NOT_USED)
    with scopes_disabled():
        Voucher.objects.filter(pk=member_voucher.voucher_id).update(redeemed=1)

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    assert [call.kwargs["email"] for call in mock_mail.call_args_list] == [stays.user.email]


@pytest.mark.django_db
def test_email_is_closed_without_sending_when_everyone_left_the_group(event, django_user_model):
    member = _member(event, django_user_model, "member@example.com")
    queue = _queue_for_group(event, RecipientGroup.WITHOUT_SHIFTS)
    _assign_shift(event, member.user)

    with patch("teamshifts.tasks.mail") as mock_mail:
        send_queued_email.run(event_id=event.pk, queue_id=queue.pk)

    mock_mail.assert_not_called()
    with scope(event=event):
        queue.refresh_from_db()
        assert not queue.recipients.exists()
    assert queue.sent_at is not None


@pytest.mark.django_db
def test_outbox_drafts_and_sent_lists_show_the_group_as_label(orga_client, event, user, django_user_model):
    member = _member(event, django_user_model, "member@example.com")
    with scope(event=event):
        queue_email(event, "Outbox mail", "m", [member.user], user=user, recipient_group=RecipientGroup.WITHOUT_SHIFTS, dispatch=False)
        queue_email(event, "Draft mail", "m", [], user=user, recipient_group=RecipientGroup.VOUCHER_NOT_USED, is_draft=True)
        sent = queue_email(event, "Sent mail", "m", [], user=user, recipient_group=RecipientGroup.WITHOUT_SHIFTS, dispatch=False)
        sent.sent_at = now()
        sent.save(update_fields=["sent_at"])
        TeamShiftsEmailQueueRecipient.objects.create(queue=sent, user=member.user, email=member.user.email, sent_at=now())

    outbox_page = orga_client.get(_url(event, "email_outbox")).content.decode()
    drafts_page = orga_client.get(_url(event, "email_drafts")).content.decode()
    sent_page = orga_client.get(_url(event, "email_sent")).content.decode()

    assert "Without shifts" in outbox_page
    assert "Voucher not used" in drafts_page
    assert "Without shifts" in sent_page


@pytest.mark.django_db
def test_specific_people_override_the_send_to_dropdown(orga_client, event, django_user_model):
    _member(event, django_user_model, "free@example.com")
    busy = _member(event, django_user_model, "busy@example.com")
    _assign_shift(event, busy.user)

    with patch("teamshifts.services.email._dispatch"):
        response = orga_client.post(
            _url(event, "email_compose"),
            {
                "action": "outbox",
                "status": RecipientGroup.WITHOUT_SHIFTS,
                "individual_volunteers": [busy.user.pk],
                "subject_0": "s",
                "message_0": "m",
                "send_after": "",
            },
        )

    assert response.status_code == 302
    with scope(event=event):
        queue = TeamShiftsEmailQueue.objects.get(event=event)
        emails = set(queue.recipients.values_list("email", flat=True))
    assert emails == {busy.user.email}
    assert (queue.individual_recipients, queue.status_filter, queue.recipient_group) == (True, "", "")
    assert "Specific people" in orga_client.get(_url(event, "email_outbox")).content.decode()


@pytest.mark.django_db
def test_preview_with_specific_people_ignores_the_send_to_dropdown(orga_client, event, django_user_model):
    _member(event, django_user_model, "free@example.com")
    busy = _member(event, django_user_model, "busy@example.com")
    _assign_shift(event, busy.user)
    url = _url(event, "email_compose")

    picked = orga_client.post(url, {"action": "preview", "status": RecipientGroup.WITHOUT_SHIFTS, "individual_volunteers": [busy.user.pk]})
    unusable = orga_client.post(url, {"action": "preview", "status": RecipientGroup.WITHOUT_SHIFTS, "individual_volunteers": ["x"]})

    assert _emails(picked.context["preview_recipients"]) == {busy.user.email}
    assert unusable.context["preview_recipients"] == []


@pytest.mark.django_db
def test_reopening_a_specific_people_draft_restores_them(orga_client, event, user, django_user_model):
    picked = _member(event, django_user_model, "picked@example.com")
    _member(event, django_user_model, "other@example.com")
    with scope(event=event):
        draft = queue_email(event, "s", "m", [picked.user], user=user, individual_recipients=True, is_draft=True)

    content = orga_client.get(_url(event, "email_compose") + f"?draft={draft.pk}").content.decode()

    assert f'<option value="{picked.user.pk}" selected>' in content
    assert f'value="{ApplicationStatus.ACCEPTED}" selected' in content

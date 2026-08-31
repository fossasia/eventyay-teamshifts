import logging
from collections.abc import Sequence
from typing import TypedDict

from django.db import transaction
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.email import get_email_context
from eventyay.base.models import Event, Voucher
from eventyay.base.services.mail import SendMailException, mail
from eventyay.multidomain.urlreverse import build_absolute_uri
from i18nfield.strings import LazyI18nString

from ..models import (
    CallForTeamMembers,
    EmailTemplateRoles,
    MemberVoucher,
    TeamMemberApplication,
    VolunteerVoucherSettings,
    VoucherStatus,
)

logger = logging.getLogger(__name__)


class SendResult(TypedDict):
    sent: int
    resent: int
    skipped_claimed: int
    skipped_no_vouchers: int


def allocate_and_send_vouchers(
    event: Event,
    settings: VolunteerVoucherSettings,
    applications: Sequence[TeamMemberApplication],
) -> SendResult:
    """Allocate voucher codes and send emails for selected members.

    Behaviour per member:
    - Not sent → pick an unused voucher from the batch, send email, status = sent
    - Sent — not claimed → resend the same code, no new voucher allocated
    - Claimed → skip silently
    """
    result: SendResult = {"sent": 0, "resent": 0, "skipped_claimed": 0, "skipped_no_vouchers": 0}

    try:
        cfm = event.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        cfm = None

    if cfm is None:
        logger.warning("[TeamShifts] No CFM for event %s, cannot resolve voucher email template", event.slug)
        return result

    template = cfm.get_mail_template(EmailTemplateRoles.VOUCHER_SENT)
    locale = event.settings.locale

    for application in applications:
        user = application.user
        if not user or not user.email:
            continue

        with scope(event=event):
            existing = MemberVoucher.objects.filter(application=application).select_related("voucher").first()

        if existing is not None:
            existing.refresh_claimed_status()
            if existing.status == VoucherStatus.CLAIMED:
                result["skipped_claimed"] += 1
                continue

            # Resend the same voucher code
            _send_voucher_email(event, user, existing.voucher, template, locale)
            if existing.status == VoucherStatus.NOT_SENT:
                existing.status = VoucherStatus.SENT
            existing.sent_at = now()
            existing.save(update_fields=["status", "sent_at"])
            result["resent"] += 1
            continue

        # Allocate a new voucher from the batch
        with scope(event=event):
            voucher = _claim_next_voucher(settings)

        if voucher is None:
            result["skipped_no_vouchers"] += 1
            continue

        with scope(event=event):
            member_voucher = MemberVoucher.objects.create(
                application=application,
                voucher=voucher,
                status=VoucherStatus.SENT,
                sent_at=now(),
            )

        _send_voucher_email(event, user, voucher, template, locale)
        result["sent"] += 1

    return result


@transaction.atomic
def _claim_next_voucher(settings: VolunteerVoucherSettings) -> Voucher | None:
    """Atomically pick and lock one unused voucher from the batch.

    Uses select_for_update to prevent two concurrent requests from
    allocating the same voucher.
    """
    assigned_ids = MemberVoucher.objects.filter(
        application__event=settings.event,
    ).values_list("voucher_id", flat=True)

    voucher = (
        Voucher.objects.filter(
            event=settings.event,
            tag=settings.voucher_tag,
            redeemed=0,
        )
        .exclude(pk__in=assigned_ids)
        .select_for_update(skip_locked=True)
        .first()
    )
    return voucher


def _send_voucher_email(event, user, voucher, template, locale):
    """Send the voucher email directly (synchronous, no outbox)."""
    redeem_base = build_absolute_uri(event, "presale:event.index")
    ticket_claim_url = f"{redeem_base}?voucher={voucher.code}"

    context = get_email_context(event=event, user=user)
    context["voucher_code"] = voucher.code
    context["ticket_claim_url"] = ticket_claim_url

    subject = LazyI18nString(template.subject)
    body = LazyI18nString(template.body)

    try:
        mail(
            email=user.email,
            subject=subject,
            template=body,
            context=context,
            event=event,
            locale=locale,
            user=user,
            auto_email=False,
            sync_send=True,
        )
    except SendMailException:
        logger.exception("[TeamShifts] Failed to send voucher email to %s", user.email)

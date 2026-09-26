import logging

from django_scopes import scope, scopes_disabled

from ..models import ApplicationStatus, MemberVoucher, ShiftAssignment, TeamMemberApplication
from .certificates import maybe_auto_issue_certificate

logger = logging.getLogger(__name__)


def resolve_volunteer_application(checkin):
    """Resolve a Checkin to a TeamMemberApplication via voucher chain or email match."""
    event = checkin.list.event
    position = checkin.position

    if position.voucher_id:
        with scopes_disabled():
            try:
                member_voucher = MemberVoucher.objects.select_related("application").get(
                    voucher_id=position.voucher_id,
                )
            except MemberVoucher.DoesNotExist:
                pass
            else:
                app = member_voucher.application
                if app.event_id == event.pk and app.status == ApplicationStatus.ACCEPTED:
                    return app

    email = position.attendee_email or position.order.email
    if email:
        with scope(event=event):
            try:
                return TeamMemberApplication.objects.get(
                    event=event,
                    user__email__iexact=email,
                    status=ApplicationStatus.ACCEPTED,
                )
            except (TeamMemberApplication.DoesNotExist, TeamMemberApplication.MultipleObjectsReturned):
                pass

    return None


def stamp_shift_start(user, event, checkin_dt):
    """Set started_at on shift assignments active at check-in time."""
    with scope(event=event):
        ShiftAssignment.objects.filter(
            team_member=user,
            shift__event=event,
            started_at__isnull=True,
            shift__start_time__lte=checkin_dt,
            shift__end_time__gt=checkin_dt,
        ).update(started_at=checkin_dt)


def handle_volunteer_checkin(checkin):
    """Wire an eventyay Checkin to teamshifts arrival tracking."""
    application = resolve_volunteer_application(checkin)
    if application is None:
        return

    event = application.event

    if not application.arrived:
        application.arrived = True
        application.save(update_fields=["arrived", "updated_at"])

    stamp_shift_start(application.user, event, checkin.datetime)
    maybe_auto_issue_certificate(application)

    logger.info(
        "[TeamShifts] Volunteer %s marked arrived via ticket check-in for event %s",
        application.user.email,
        event.slug,
    )


def end_shift(assignment, end_dt):
    """Mark a shift assignment as completed and trigger certificate evaluation."""
    assignment.ended_at = end_dt
    assignment.save(update_fields=["ended_at"])

    with scope(event=assignment.shift.event):
        try:
            application = TeamMemberApplication.objects.get(
                event=assignment.shift.event,
                user=assignment.team_member,
                status=ApplicationStatus.ACCEPTED,
            )
        except TeamMemberApplication.DoesNotExist:
            return

    maybe_auto_issue_certificate(application)

import logging
from collections.abc import Sequence
from typing import TypedDict

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now
from django_scopes import scope
from eventyay.base.models import CartPosition, Event, User
from eventyay.person.services import create_user

from ..models import (
    ApplicationStatus,
    CallForTeamMembers,
    EmailTemplateRoles,
    MemberVoucher,
    ShiftAssignment,
    TeamApplicationAnswer,
    TeamMemberApplication,
)
from .email import queue_lifecycle_email

logger = logging.getLogger(__name__)


class AlreadyMemberError(Exception):
    def __init__(self, application: TeamMemberApplication):
        self.application = application
        super().__init__("User is already an accepted team member for this event.")


def resolve_or_create_user(*, email: str, full_name: str = "") -> tuple[User, bool]:
    email = email.lower().strip()
    full_name = (full_name or "").strip()
    existing = User.objects.filter(email__iexact=email).first()
    if existing:
        if full_name and not existing.fullname:
            existing.fullname = full_name
            existing.save(update_fields=["fullname"])
        return existing, False
    return create_user(email=email, name=full_name, event=None), True


def add_member_from_organizer(*, event, form) -> TeamMemberApplication:
    """Create or accept a team member application from organizer-provided form data."""
    email = form.cleaned_data["email"]
    full_name = form.cleaned_data.get("full_name", "")
    phone = form.cleaned_data.get("phone", "")
    availability_notes = form.cleaned_data.get("availability_notes", "")
    answers = form.get_question_answers()

    with transaction.atomic():
        user, _created = resolve_or_create_user(email=email, full_name=full_name)
        with scope(event=event):
            application = TeamMemberApplication.objects.filter(event=event, user=user).first()
            if application and application.status == ApplicationStatus.ACCEPTED:
                raise AlreadyMemberError(application)

            if application:
                application.status = ApplicationStatus.ACCEPTED
                application.phone = phone
                application.availability_notes = availability_notes
                application.added_by_organizer = True
                application.save(update_fields=["status", "phone", "availability_notes", "added_by_organizer", "updated_at"])
                application.answers.all().delete()
            else:
                application = TeamMemberApplication.objects.create(
                    event=event,
                    user=user,
                    status=ApplicationStatus.ACCEPTED,
                    phone=phone,
                    availability_notes=availability_notes,
                    added_by_organizer=True,
                )

            for question, answer_text in answers:
                TeamApplicationAnswer.objects.create(
                    application=application,
                    question=question,
                    answer=answer_text,
                )

    return application


def accept_manager_as_member(*, event, user) -> tuple[TeamMemberApplication, bool]:
    with scope(event=event):
        application, created = TeamMemberApplication.objects.get_or_create(
            event=event,
            user=user,
            defaults={"status": ApplicationStatus.ACCEPTED, "added_by_organizer": True},
        )
        promoted = not created and application.status != ApplicationStatus.ACCEPTED
        if promoted:
            application.status = ApplicationStatus.ACCEPTED
            application.added_by_organizer = True
            application.save(update_fields=["status", "added_by_organizer", "updated_at"])
    return application, promoted


class RemovalResult(TypedDict):
    removed: int
    freed_spots: int
    emails_queued: int
    vouchers_cancelled: int


def upcoming_assignment_filter(when=None) -> Q:
    """Shift assignments that are still ahead of the member, so removing them frees the spot.

    An early check-in (within the grace window before the shift starts) already counts as
    shift history, so that assignment is kept like a started one.
    """
    return Q(shift__start_time__gt=when or now(), started_at__isnull=True)


def removed_event_ids(user):
    """Events the user was removed from. Their shifts there are no longer listed for them."""
    return TeamMemberApplication.objects.filter(user=user, status=ApplicationStatus.REMOVED).values("event_id")


IN_PROGRESS_ASSIGNMENT = Q(started_at__isnull=False, ended_at__isnull=True)


def hidden_after_removal_filter(user) -> Q:
    """Assignments a removed member no longer sees: everything in that event except a shift
    they had already started and not yet checked out of, which they may still close."""
    return Q(shift__event_id__in=removed_event_ids(user)) & ~IN_PROGRESS_ASSIGNMENT


def remove_members_from_team(
    *,
    event: Event,
    application_ids: Sequence[int],
    user: User | None,
    notify: bool,
    cancel_vouchers: bool,
) -> RemovalResult:
    """Take accepted members off the team and off their upcoming shifts.

    Assignments for shifts that have started or ended stay, with their check-in data.
    Certificates are left alone. The rejection email is never sent here.
    """
    result: RemovalResult = {"removed": 0, "freed_spots": 0, "emails_queued": 0, "vouchers_cancelled": 0}
    with scope(event=event), transaction.atomic():
        applications = list(
            TeamMemberApplication.objects.select_for_update(of=("self",))
            .filter(event=event, pk__in=application_ids, status=ApplicationStatus.ACCEPTED)
            .select_related("user")
        )
        if not applications:
            return result
        # Shift claims and organizer assignments lock the member's user row before writing,
        # so taking the same locks here keeps either side from seeing a half-done removal.
        list(User.objects.select_for_update().filter(pk__in=[app.user_id for app in applications]).order_by("pk"))

        upcoming = ShiftAssignment.objects.filter(
            upcoming_assignment_filter(),
            shift__event=event,
            team_member_id__in=[app.user_id for app in applications],
        )
        result["freed_spots"] = upcoming.count()
        upcoming.delete()

        TeamMemberApplication.objects.filter(pk__in=[app.pk for app in applications]).update(status=ApplicationStatus.REMOVED, updated_at=now())
        result["removed"] = len(applications)

        if cancel_vouchers:
            result["vouchers_cancelled"] = _cancel_unused_vouchers(event, applications, user)

        # queue_lifecycle_email skips events without a Call for Team Members, so don't count those.
        notify = notify and CallForTeamMembers.objects.filter(event=event).exists()
        for app in applications:
            app.status = ApplicationStatus.REMOVED
            if notify and app.user.email:
                transaction.on_commit(lambda app=app: _queue_removal_email_safely(app))
                result["emails_queued"] += 1
    return result


def _queue_removal_email_safely(application: TeamMemberApplication) -> None:
    # Runs after the removal has committed: one failing email must not abort the others
    # or turn the request into an error, since a retry can no longer select these members.
    try:
        queue_lifecycle_email(application, EmailTemplateRoles.MEMBER_REMOVED)
    except Exception:
        logger.exception("[TeamShifts] Failed to queue removal email for application %s", application.pk)


def _cancel_unused_vouchers(event: Event, applications: Sequence[TeamMemberApplication], user: User | None) -> int:
    """Delete the members' vouchers that were never used, the same way Tickets → Vouchers deletes one."""
    member_vouchers = list(MemberVoucher.objects.filter(event=event, application__in=applications).select_related("voucher"))
    cancelled = 0
    with scope(organizer=event.organizer):
        for member_voucher in member_vouchers:
            voucher = member_voucher.voucher
            if not voucher.allow_delete():
                continue
            voucher.log_action("eventyay.voucher.deleted", user=user)
            CartPosition.objects.filter(addon_to__voucher=voucher).delete()
            voucher.cartposition_set.all().delete()
            voucher.delete()
            cancelled += 1
    return cancelled

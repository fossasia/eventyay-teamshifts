import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.html import format_html
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django_scopes import scope, scopes_disabled
from eventyay.base.email import SimpleFunctionalMailTextPlaceholder
from eventyay.base.models.organizer import Team
from eventyay.base.signals import email_filter, register_mail_placeholders
from eventyay.common.signals import periodic_task, user_menu_items
from eventyay.control.signals import event_dashboard_components, event_dashboard_widgets, nav_global
from eventyay.multidomain.urlreverse import build_absolute_uri
from eventyay.presale.signals import header_nav_tabs

from .models import ApplicationStatus, CallForTeamMembers, ShiftAssignment, TeamMemberApplication, TeamRole, TeamShiftsEmailQueue
from .permissions import has_any_teamshifts_permission
from .services.email import html_to_plain_text, looks_like_email_html
from .tasks import send_queued_email

logger = logging.getLogger(__name__)


@receiver(event_dashboard_widgets, dispatch_uid="teamshifts_dashboard_widget")
def teamshifts_dashboard_widget(sender, subevent=None, lazy=False, request=None, **kwargs):
    if request is None or not has_any_teamshifts_permission(request.user, request.organizer, sender, request=request):
        return []
    return [
        {
            "content": '<div class="numwidget"><span class="num">-</span><span class="text">{}</span></div>'.format(str(_("TeamShifts"))),
            "display_size": "small",
            "priority": 80,
            "url": reverse(
                "plugins:teamshifts:dashboard",
                kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
            ),
        }
    ]


@receiver(event_dashboard_components, dispatch_uid="teamshifts_dashboard_component")
def teamshifts_dashboard_component(sender, request=None, **kwargs):
    if request is None or not has_any_teamshifts_permission(request.user, request.organizer, sender, request=request):
        return ""
    url = reverse(
        "plugins:teamshifts:dashboard",
        kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
    )
    return format_html(
        '<div class="panel panel-default widget-container widget-small no-padding last-column">'
        '<div class="panel-heading"><h3 class="panel-title">{}</h3></div>'
        '<div class="panel-body"><p>{}</p><p>{} <a href="{}">{}</a></p></div>'
        "</div>",
        str(_("TeamShifts")),
        str(_("Manage event teams, define team roles, review applications, and schedule shifts.")),
        str(_("Go to")),
        url,
        str(_("TeamShifts Dashboard")),
    )


@receiver(header_nav_tabs, dispatch_uid="teamshifts_header_nav_tab")
def teamshifts_header_nav_tab(sender, request=None, **kwargs):
    try:
        cfm = sender.call_for_team_members
    except CallForTeamMembers.DoesNotExist:
        return ""
    if not cfm.effective_show_on_menu:
        return ""
    apply_url = reverse(
        "plugins:teamshifts:apply",
        kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
    )
    is_active = request is not None and getattr(request, "resolver_match", None) is not None and request.resolver_match.url_name == "apply"
    tab_title = cfm.title if cfm.is_open else format_html("{} ({})", cfm.title, _("Closed"))
    return format_html(
        '<a href="{}" class="header-tab {}"><i class="fa fa-users"></i> {}</a>',
        apply_url,
        "active" if is_active else "",
        tab_title,
    )


@receiver(header_nav_tabs, dispatch_uid="teamshifts_public_schedule_nav_tab")
def teamshifts_public_schedule_nav_tab(sender, request=None, **kwargs):
    if request is None or not request.user.is_authenticated:
        return ""
    from django_scopes import scope as _scope

    with _scope(event=sender):
        is_accepted = TeamMemberApplication.objects.filter(
            event=sender,
            user=request.user,
            status=ApplicationStatus.ACCEPTED,
        ).exists()
    if not is_accepted:
        return ""
    schedule_url = reverse(
        "plugins:teamshifts:public_shift_schedule",
        kwargs={"organizer": sender.organizer.slug, "event": sender.slug},
    )
    is_active = request is not None and "/teamshifts/shifts/" in getattr(request, "path_info", "")
    return format_html(
        '<a href="{}" class="header-tab {}"><i class="fa fa-calendar-check-o"></i> {}</a>',
        schedule_url,
        "active" if is_active else "",
        _("Shift Schedule"),
    )


@receiver(email_filter, dispatch_uid="teamshifts_email_plain_text_body")
def teamshifts_email_plain_text_body(sender, message, order=None, user=None, **kwargs):
    """Keep the text/plain MIME part free of raw Tiptap HTML tags (#116)."""
    body = getattr(message, "body", None) or ""
    if looks_like_email_html(body):
        message.body = html_to_plain_text(body)
    return message


@receiver(register_mail_placeholders, dispatch_uid="teamshifts_mail_placeholders")
def teamshifts_mail_placeholders(sender, **kwargs):
    return [
        SimpleFunctionalMailTextPlaceholder(
            "full_name",
            ["user"],
            lambda user: (getattr(user, "fullname", "") or user.email) if user else "",
            lambda event: _("Volunteer"),
        ),
        SimpleFunctionalMailTextPlaceholder(
            "role_name",
            ["role"],
            lambda role: role.name,
            lambda event: _("Volunteer role"),
        ),
        SimpleFunctionalMailTextPlaceholder(
            "event_name",
            ["event"],
            lambda event: str(event.name),
            lambda event: str(event.name),
        ),
        SimpleFunctionalMailTextPlaceholder(
            "event_dates",
            ["event"],
            lambda event: event.get_date_range_display(),
            lambda event: event.get_date_range_display(),
        ),
        SimpleFunctionalMailTextPlaceholder(
            "event_location",
            ["event"],
            lambda event: str(event.location) if event.location else "",
            lambda event: str(event.location) if event.location else _("(no location set)"),
        ),
        SimpleFunctionalMailTextPlaceholder(
            "shift_schedule_url",
            ["event"],
            lambda event: build_absolute_uri(event, "presale:event.index"),
            lambda event: "https://example.com/my-event/",
        ),
    ]


PLUGIN_NAME = "teamshifts"


def _has_shifts_in_active_events(user):
    return ShiftAssignment.objects.filter(team_member=user, shift__event__plugins__contains=PLUGIN_NAME).exists()


@receiver(user_menu_items, dispatch_uid="teamshifts_user_menu_item")
def teamshifts_user_menu_item(sender, request=None, icon_class="", **kwargs):
    if request is None or not request.user.is_authenticated:
        return ""
    if PLUGIN_NAME not in sender.get_plugins():
        return ""
    with scope(event=sender):
        has_shifts = ShiftAssignment.objects.filter(shift__event=sender, team_member=request.user).exists()
    if not has_shifts:
        return ""
    return format_html(
        '<a href="{}" class="dropdown-item" role="menuitem" tabindex="-1"><i class="fa fa-calendar-check-o {}"></i> {}</a>',
        reverse("plugins:teamshifts:my_shifts_global"),
        icon_class,
        _("My shifts"),
    )


@receiver(nav_global, dispatch_uid="teamshifts_nav_global_my_shifts")
def teamshifts_nav_global_my_shifts(sender, request=None, **kwargs):
    if request is None or not getattr(request, "user", None) or not request.user.is_authenticated:
        return []
    with scopes_disabled():
        if not _has_shifts_in_active_events(request.user):
            return []
    return [
        {
            "label": _("My Shifts"),
            "url": reverse("plugins:teamshifts:my_shifts_global"),
            "active": "/common/my-shifts/" in getattr(request, "path_info", ""),
            "icon": "calendar-check-o",
        }
    ]


try:
    from eventyay.common.signals import user_dashboard_links

    @receiver(user_dashboard_links, dispatch_uid="teamshifts_user_dashboard_link")
    def teamshifts_user_dashboard_link(sender, **kwargs):
        """Add 'My Shifts' to the global dashboard dropdown for users with any shift assignment."""
        request = sender
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return ""
        with scopes_disabled():
            if not _has_shifts_in_active_events(request.user):
                return ""
        return format_html(
            '<a href="{}" class="dropdown-item" role="menuitem" tabindex="-1"><i class="fa fa-calendar-check-o"></i> {}</a>',
            reverse("plugins:teamshifts:my_shifts_global"),
            _("My shifts"),
        )

except ImportError:
    pass


@receiver(periodic_task, dispatch_uid="teamshifts_dispatch_scheduled_emails")
@scopes_disabled()
def dispatch_scheduled_emails(sender, **kwargs):
    MAIL_SEND_BATCH_SIZE = 50
    with transaction.atomic():
        due = list(
            TeamShiftsEmailQueue.objects.filter(
                send_after__isnull=False,
                send_after__lte=now(),
                sent_at__isnull=True,
            )
            .select_for_update(skip_locked=True)
            .order_by("pk")
            .values_list("pk", "event_id")[:MAIL_SEND_BATCH_SIZE]
        )
    for queue_pk, event_id in due:
        cache_key = f"teamshifts_mail_queue_{queue_pk}_enqueued"
        if cache.add(cache_key, True, timeout=300):
            send_queued_email.delay(event_id, queue_pk)
            logger.info("[TeamShifts] Dispatched scheduled email queue %s", queue_pk)


@receiver(post_delete, sender=TeamRole)
@scopes_disabled()
def team_role_post_delete(sender, instance, **kwargs):
    teams = Team.objects.filter(
        organizer=instance.event.organizer,
        limit_teamshifts_roles__contains=[instance.pk],
    )
    for team in teams:
        team.limit_teamshifts_roles.remove(instance.pk)
        team.save(update_fields=["limit_teamshifts_roles"])

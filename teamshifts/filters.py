from datetime import datetime, time

from django import forms
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms.widgets import DatePickerWidget
from eventyay.control.forms.filter import FilterForm

from .models import (
    ApplicationStatus,
    CertificateStatus,
    EmailTemplateRoles,
    ShiftAssignment,
    ShiftLocation,
    ShiftRoleAssignment,
    TeamRole,
    VoucherStatus,
)


def search_widget(placeholder, aria_label):
    return forms.TextInput(
        attrs={
            "class": "form-control",
            "placeholder": placeholder,
            "aria-label": aria_label,
        }
    )


def date_filter_field(label):
    return forms.DateField(
        label=label,
        required=False,
        widget=DatePickerWidget(attrs={"placeholder": label}),
    )


class TeamShiftsFilterForm(FilterForm):
    default_order = None

    def __init__(self, data=None, *args, **kwargs):
        if data is not None and "q" in data and "query" not in data:
            data = data.copy()
            data["query"] = data["q"]
        super().__init__(data, *args, **kwargs)
        self.fields.pop("ordering", None)

    @property
    def advanced_fields(self):
        return [self[name] for name in self.fields if name != "query"]

    def apply_ordering(self, queryset):
        if self.default_order:
            default_list = list(self.default_order) if isinstance(self.default_order, (list, tuple)) else [self.default_order]
            if "pk" not in default_list and "-pk" not in default_list:
                tie_breaker = "-pk" if any(f.startswith("-") for f in default_list) else "pk"
                default_list.append(tie_breaker)
            return queryset.order_by(*default_list)
        return queryset


class DateRangeFilterMixin:
    date_field = None

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", _("End date cannot be earlier than start date."))
        return cleaned_data

    def filter_date_range(self, queryset, field_name=None):
        target_field = field_name or self.date_field
        fdata = self.cleaned_data
        tz = getattr(self.event, "tz", None) or timezone.get_current_timezone()
        if date_from := fdata.get("date_from"):
            dt_from = timezone.make_aware(datetime.combine(date_from, time.min), tz)
            queryset = queryset.filter(**{f"{target_field}__gte": dt_from})
        if date_to := fdata.get("date_to"):
            dt_to = timezone.make_aware(datetime.combine(date_to, time.max), tz)
            queryset = queryset.filter(**{f"{target_field}__lte": dt_to})
        return queryset


class LenientModelChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        try:
            return super().to_python(value)
        except (forms.ValidationError, ValueError, TypeError, OverflowError):
            return None

    def validate(self, value):
        return None


def filter_by_assigned_shift_role(queryset, event, role):
    """Filter application or member querysets to users with a shift assignment in ``role``."""
    matching_users = ShiftAssignment.objects.filter(
        role=role,
        shift__event=event,
    ).values("team_member_id")
    return queryset.filter(user_id__in=matching_users)


class ApplicationFilterForm(TeamShiftsFilterForm):
    default_order = ("-created_at", "-pk")

    query = forms.CharField(
        label=_("Search applications…"),
        widget=search_widget(_("Search by name or email…"), _("Search applications")),
        required=False,
    )
    status = forms.ChoiceField(
        label=_("Status"),
        choices=(("", _("All statuses")),) + tuple(ApplicationStatus.choices),
        required=False,
    )
    role = LenientModelChoiceField(
        label=_("Assigned shift role"),
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
    )

    def __init__(self, *args, event=None, hide_emails=False, **kwargs):
        self.event = event
        self.hide_emails = hide_emails
        super().__init__(*args, **kwargs)
        if event:
            self.fields["role"].queryset = TeamRole.objects.filter(event=event)
        if hide_emails:
            self.fields["query"].widget.attrs.update(
                {
                    "placeholder": _("Search by name…"),
                    "aria-label": _("Search applications by name"),
                }
            )

    def filter_qs(self, queryset):
        fdata = self.cleaned_data

        if query := (fdata.get("query") or "").strip():
            lookup = Q(user__fullname__icontains=query)
            if not self.hide_emails:
                lookup |= Q(user__email__icontains=query)
            queryset = queryset.filter(lookup)

        if fdata.get("status"):
            queryset = queryset.filter(status=fdata["status"])

        if role := fdata.get("role"):
            queryset = filter_by_assigned_shift_role(queryset, self.event, role)

        return self.apply_ordering(queryset)


class MemberFilterForm(TeamShiftsFilterForm):
    default_order = ("user__fullname", "user__email", "pk")

    query = forms.CharField(
        label=_("Search members…"),
        widget=search_widget(_("Search by name or email…"), _("Search members")),
        required=False,
    )
    role = LenientModelChoiceField(
        label=_("Role"),
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
    )
    voucher = forms.ChoiceField(
        label=_("Voucher"),
        choices=(("", _("All voucher statuses")),) + tuple(VoucherStatus.choices),
        required=False,
    )
    arrived = forms.ChoiceField(
        label=_("Arrived"),
        choices=(
            ("", _("All")),
            ("yes", _("Arrived")),
            ("no", _("Not arrived")),
        ),
        required=False,
    )
    certificate = forms.ChoiceField(
        label=_("Certificate"),
        choices=(("", _("All certificate statuses")),) + tuple(CertificateStatus.choices),
        required=False,
    )

    def __init__(self, *args, event=None, hide_emails=False, vouchers_enabled=False, **kwargs):
        self.event = event
        self.hide_emails = hide_emails
        self.vouchers_enabled = vouchers_enabled
        super().__init__(*args, **kwargs)
        if event:
            self.fields["role"].queryset = TeamRole.objects.filter(event=event)
        if not vouchers_enabled and "voucher" in self.fields:
            del self.fields["voucher"]
        if hide_emails:
            self.fields["query"].widget.attrs.update(
                {
                    "placeholder": _("Search by name…"),
                    "aria-label": _("Search members by name"),
                }
            )

    @staticmethod
    def voucher_status_filter(voucher_status):
        # A redeemed voucher counts as claimed even before its status has been synced.
        claimed = Q(voucher_assignment__status=VoucherStatus.CLAIMED) | Q(voucher_assignment__voucher__redeemed__gt=0)
        if voucher_status == VoucherStatus.CLAIMED:
            return claimed
        if voucher_status == VoucherStatus.SENT:
            return Q(voucher_assignment__status=VoucherStatus.SENT) & ~claimed
        return (Q(voucher_assignment__isnull=True) | Q(voucher_assignment__status=VoucherStatus.NOT_SENT)) & ~claimed

    def filter_qs(self, queryset):
        fdata = self.cleaned_data

        if query := (fdata.get("query") or "").strip():
            lookup = Q(user__fullname__icontains=query)
            if not self.hide_emails:
                lookup |= Q(user__email__icontains=query)
            queryset = queryset.filter(lookup)

        if role := fdata.get("role"):
            queryset = filter_by_assigned_shift_role(queryset, self.event, role)

        voucher_status = fdata.get("voucher")
        if self.vouchers_enabled and voucher_status in VoucherStatus.values:
            queryset = queryset.filter(self.voucher_status_filter(voucher_status))

        arrived = fdata.get("arrived")
        if arrived in ("yes", "1", "true"):
            queryset = queryset.filter(arrived=True)
        elif arrived in ("no", "0", "false"):
            queryset = queryset.filter(arrived=False)

        certificate_status = fdata.get("certificate")
        if certificate_status in CertificateStatus.values:
            queryset = queryset.filter(self.certificate_status_filter(certificate_status))

        return self.apply_ordering(queryset)

    @staticmethod
    def certificate_status_filter(certificate_status):
        downloaded = Q(certificate__downloaded_at__isnull=False)
        sent = Q(certificate__notified_at__isnull=False) & ~downloaded
        generated = (
            Q(certificate__file__isnull=False) & ~Q(certificate__file="") & Q(certificate__notified_at__isnull=True, certificate__downloaded_at__isnull=True)
        )
        if certificate_status == CertificateStatus.DOWNLOADED:
            return downloaded
        if certificate_status == CertificateStatus.SENT:
            return sent
        return generated if certificate_status == CertificateStatus.GENERATED else ~downloaded & ~sent & ~generated


class ShiftFilterForm(DateRangeFilterMixin, TeamShiftsFilterForm):
    default_order = ("start_time", "pk")
    date_field = "start_time"

    query = forms.CharField(
        label=_("Search shifts…"),
        widget=search_widget(_("Search shifts by name…"), _("Search shifts")),
        required=False,
    )
    location = LenientModelChoiceField(
        label=_("Location"),
        queryset=ShiftLocation.objects.none(),
        required=False,
        empty_label=_("All locations"),
    )
    role = LenientModelChoiceField(
        label=_("Role"),
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
    )
    date_from = date_filter_field(_("Start date"))
    date_to = date_filter_field(_("End date"))
    staffing = forms.ChoiceField(
        label=_("Staffing"),
        choices=(
            ("", _("All staffing")),
            ("open", _("Has open spots")),
            ("full", _("Fully staffed")),
        ),
        required=False,
    )

    def __init__(self, *args, event=None, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        if event:
            self.fields["location"].queryset = ShiftLocation.objects.filter(event=event)
            self.fields["role"].queryset = TeamRole.objects.filter(event=event)

    def filter_qs(self, queryset):
        fdata = self.cleaned_data

        if query := (fdata.get("query") or "").strip():
            queryset = queryset.filter(name__icontains=query)

        if fdata.get("location"):
            queryset = queryset.filter(location=fdata["location"])

        if fdata.get("role"):
            queryset = queryset.filter(role_assignments__role=fdata["role"]).distinct()

        queryset = self.filter_date_range(queryset)

        if staffing := fdata.get("staffing"):
            assigned = (
                ShiftAssignment.objects.filter(shift=OuterRef("shift"), role=OuterRef("role")).values("shift", "role").annotate(cnt=Count("id")).values("cnt")
            )
            open_slots = (
                ShiftRoleAssignment.objects.filter(shift__event=self.event)
                .annotate(assigned=Coalesce(Subquery(assigned, output_field=IntegerField()), 0))
                .filter(assigned__lt=F("capacity"))
            )
            if staffing == "open":
                queryset = queryset.filter(role_assignments__in=open_slots).distinct()
            else:
                queryset = queryset.filter(role_assignments__isnull=False).exclude(role_assignments__in=open_slots).distinct()

        return self.apply_ordering(queryset)


class TeamShiftsEmailFilterForm(DateRangeFilterMixin, TeamShiftsFilterForm):
    date_field = "created"

    query = forms.CharField(
        label=_("Search emails…"),
        widget=search_widget(_("Search by subject or recipient…"), _("Search emails")),
        required=False,
    )
    role = LenientModelChoiceField(
        label=_("Role"),
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
    )
    date_from = date_filter_field(_("Start date"))
    date_to = date_filter_field(_("End date"))

    def __init__(self, *args, event=None, date_field="created", hide_emails=False, **kwargs):
        self.event = event
        self.date_field = date_field
        self.hide_emails = hide_emails
        self.default_order = (f"-{date_field}", "-pk")
        super().__init__(*args, **kwargs)
        if event:
            self.fields["role"].queryset = TeamRole.objects.filter(event=event)
        if hide_emails:
            self.fields["query"].widget.attrs.update(
                {
                    "placeholder": _("Search by subject…"),
                    "aria-label": _("Search emails by subject"),
                }
            )

    def filter_qs(self, queryset):
        fdata = self.cleaned_data

        if query := (fdata.get("query") or "").strip():
            if self.hide_emails:
                queryset = queryset.filter(subject__icontains=query)
            else:
                recipient_matches = queryset.model.objects.filter(recipients__email__icontains=query).values("pk")
                queryset = queryset.filter(Q(subject__icontains=query) | Q(pk__in=recipient_matches))

        if role := fdata.get("role"):
            queryset = queryset.filter(role_filter=role)

        queryset = self.filter_date_range(queryset)

        return self.apply_ordering(queryset)


class TeamShiftsSentEmailFilterForm(DateRangeFilterMixin, TeamShiftsFilterForm):
    default_order = ("-sent_at", "-pk")
    date_field = "sent_at"

    query = forms.CharField(
        label=_("Search emails…"),
        widget=search_widget(_("Search by subject or recipient…"), _("Search emails")),
        required=False,
    )
    role = LenientModelChoiceField(
        label=_("Role"),
        queryset=TeamRole.objects.none(),
        required=False,
        empty_label=_("All roles"),
    )
    mail_type = forms.ChoiceField(
        label=_("Email type"),
        required=False,
    )
    date_from = date_filter_field(_("Start date"))
    date_to = date_filter_field(_("End date"))

    def __init__(self, *args, event=None, hide_emails=False, **kwargs):
        self.event = event
        self.hide_emails = hide_emails
        super().__init__(*args, **kwargs)
        if event:
            self.fields["role"].queryset = TeamRole.objects.filter(event=event)
        self.fields["mail_type"].choices = [("", _("All types")), ("custom", _("Custom"))] + list(EmailTemplateRoles.choices)
        if hide_emails:
            self.fields["query"].widget.attrs.update(
                {
                    "placeholder": _("Search by subject or name…"),
                    "aria-label": _("Search emails by subject or name"),
                }
            )

    def filter_qs(self, queryset):
        fdata = self.cleaned_data

        if query := (fdata.get("query") or "").strip():
            condition = Q(subject__icontains=query) | Q(subject="", queue__subject__icontains=query) | Q(user__fullname__icontains=query)
            if not self.hide_emails:
                condition |= Q(email__icontains=query)
            queryset = queryset.filter(condition)

        if role := fdata.get("role"):
            queryset = queryset.filter(queue__role_filter=role)

        if mail_type := fdata.get("mail_type"):
            if mail_type == "custom":
                queryset = queryset.filter(queue__mail_type="")
            else:
                queryset = queryset.filter(queue__mail_type=mail_type)

        queryset = self.filter_date_range(queryset)

        return self.apply_ordering(queryset)

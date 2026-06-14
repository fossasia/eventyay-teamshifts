from django import forms
from django.utils.translation import gettext_lazy as _
from django_scopes import scopes_disabled

from .models import CallForTeamMembers, TeamMemberApplication, TeamRole


class CallForTeamMembersForm(forms.ModelForm):
    """
    Organiser form to configure the Call for Team Members settings.
    """

    class Meta:
        model = CallForTeamMembers
        fields = ("title", "active", "deadline", "description")
        widgets = {
            "deadline": forms.DateTimeInput(
                attrs={"class": "form-control datetimepicker"},
                format="%Y-%m-%d %H:%M:%S",
            ),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("e.g. Call for Volunteers, Call for Staff"),
                },
            ),
        }


class TeamRoleForm(forms.ModelForm):
    """
    Organiser form to create or update a TeamRole.
    """

    class Meta:
        model = TeamRole
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class TeamMemberApplicationForm(forms.Form):
    """
    Public form for team member sign-up.
    Shown at /teamshifts/event/<org>/<event>/apply/.

    Uses a plain Form (not ModelForm) to avoid ScopedManager firing at
    import time when Django's ModelForm metaclass evaluates FK querysets.
    """

    role = forms.ModelChoiceField(
        queryset=TeamRole.objects.none(),
        label=_("Role you are applying for"),
        empty_label=_("— Select a role —"),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    availability_notes = forms.CharField(
        required=False,
        label=_("Availability notes"),
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": _("Describe your availability, any constraints, or additional information."),
            }
        ),
    )

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        if event is not None:
            with scopes_disabled():
                self.fields["role"].queryset = TeamRole.objects.filter(event=event)

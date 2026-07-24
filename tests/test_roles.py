import pytest
from django.core.exceptions import ValidationError
from django_scopes import scope

from teamshifts.forms import TeamRoleForm
from teamshifts.models import TeamRole


@pytest.mark.django_db
def test_teamrole_is_restricted_default(event):
    with scope(event=event):
        role = TeamRole.objects.create(event=event, name="Open Role")
        assert role.is_restricted is False


@pytest.mark.django_db
def test_teamrole_form_validates_is_restricted(event):
    with scope(event=event):
        form = TeamRoleForm(data={"name": "Manager", "is_restricted": True})
        form.instance.event = event
        assert form.is_valid()
        role = form.save()
        assert role.is_restricted is True


@pytest.mark.django_db
def test_teamrole_form_validates_open_role(event):
    with scope(event=event):
        form = TeamRoleForm(data={"name": "Helper", "is_restricted": False})
        form.instance.event = event
        assert form.is_valid()
        role = form.save()
        assert role.is_restricted is False

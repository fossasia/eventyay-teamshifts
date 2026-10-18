
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_add_role_supports_zero_existing_rows():
    """Adding a role must work when every existing role row has been removed."""
    javascript = (
        ROOT / "teamshifts/static/teamshifts/shift_role_formset.js"
    ).read_text()

    assert 'document.getElementById("role-form-template")' in javascript
    assert "const templateRow = rowTemplate?.content.firstElementChild;" in javascript
    assert "const newRow = lastRow" in javascript
    assert ": templateRow?.cloneNode(true);" in javascript
    assert "totalFormsInput.value = formCount + 1;" in javascript


def test_create_and_edit_templates_include_empty_role_form():
    """Both shift forms must provide an empty role-row template."""
    templates = [
        ROOT / "teamshifts/templates/teamshifts/shift_create.html",
        ROOT / "teamshifts/templates/teamshifts/shift_edit.html",
    ]

    for template_path in templates:
        template = template_path.read_text()

        assert '<template id="role-form-template">' in template
        assert "{% with form=formset.empty_form %}" in template
        assert 'class="role-form-row"' in template

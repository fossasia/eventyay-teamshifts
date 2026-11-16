// Shared dynamic-formset logic for the shift create/edit "Roles Needed" table,
// so the two pages don't duplicate (and drift out of sync with) this logic.

export function setupDynamicFormset() {
    const addRoleBtn = document.getElementById("add-role-btn");
    const rolesBody = document.getElementById("roles-body");
    const totalFormsInput = document.getElementById("id_roles-TOTAL_FORMS");
    const rowTemplate = document.getElementById("role-form-template");

    if (!addRoleBtn || !rolesBody || !totalFormsInput) {
        return;
    }

    addRoleBtn.addEventListener("click", () => {
        const formCount = parseInt(totalFormsInput.value, 10) || 0;

        const templateRow = rowTemplate?.content.firstElementChild;
        const newRow = templateRow?.cloneNode(true);

        if (!newRow) {
            return;
        }

        const reindex = (val) =>
            val ? val.replace(/roles-(?:\d+|__prefix__)-/, `roles-${formCount}-`) : val;
        newRow.querySelectorAll("input, select, label, textarea").forEach((el) => {
            if (el.name) el.name = reindex(el.name);
            if (el.id) el.id = reindex(el.id);
            if (el.htmlFor) el.htmlFor = reindex(el.htmlFor);
        });

        newRow.querySelectorAll("input, select").forEach((input) => {
            if (input.type === "checkbox") {
                input.checked = false;
            } else if (input.tagName === "SELECT") {
                input.selectedIndex = 0;
            } else if (input.type !== "hidden" || input.name.endsWith("-id")) {
                input.value = input.name.endsWith("-capacity") ? "1" : "";
            }
        });

        newRow.querySelectorAll(".help-block").forEach((err) => err.remove());
        newRow.querySelectorAll(".has-error").forEach((cell) => cell.classList.remove("has-error"));
        newRow.style.display = "";

        rolesBody.appendChild(newRow);
        totalFormsInput.value = formCount + 1;
    });
}

export function bindRemoveRowHandler() {
    const rolesBody = document.getElementById("roles-body");
    if (!rolesBody) {
        return;
    }

    rolesBody.addEventListener("click", (event) => {
        const btn = event.target.closest(".remove-role-btn");
        if (!btn) {
            return;
        }

        const row = btn.closest(".role-form-row");
        if (!row) {
            return;
        }

        const deleteCheckbox = row.querySelector('.delete-checkbox input[type="checkbox"]');
        const idInput = row.querySelector('input[name$="-id"]');
        const hasExistingRecord = idInput && idInput.value;

        if (hasExistingRecord && deleteCheckbox) {
            deleteCheckbox.checked = true;
            row.style.display = "none";
        } else {
            row.remove();
            const totalFormsInput = document.getElementById("id_roles-TOTAL_FORMS");
            if (totalFormsInput) {
                const newTotal = Math.max(0, parseInt(totalFormsInput.value, 10) - 1);
                totalFormsInput.value = newTotal;
                const remainingRows = rolesBody.querySelectorAll(".role-form-row");
                remainingRows.forEach((r, idx) => {
                    r.querySelectorAll("input, select, label, textarea").forEach((el) => {
                        if (el.name) el.name = el.name.replace(/roles-\d+-/, `roles-${idx}-`);
                        if (el.id) el.id = el.id.replace(/roles-\d+-/, `roles-${idx}-`);
                        if (el.htmlFor) el.htmlFor = el.htmlFor.replace(/roles-\d+-/, `roles-${idx}-`);
                    });
                });
            }
        }
    });
}

export function markFormsetJsEnabled() {
    const rolesTable = document.getElementById("roles-table");
    if (rolesTable) {
        rolesTable.classList.add("js-formset-enabled");
    }
}

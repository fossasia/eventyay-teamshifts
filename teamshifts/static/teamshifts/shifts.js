function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
}

function getI18n() {
    const el = document.querySelector("[data-teamshifts-shifts-i18n]");
    return el ? el.dataset : {};
}

function setCancelButtonHidden(hidden) {
    const cancelBtn = document.getElementById("pretix-confirm-dialog-cancel");
    if (cancelBtn) {
        cancelBtn.classList.toggle("teamshifts-dialog-cancel-hidden", hidden);
    }
}

function showAlertDialog(message) {
    const i18n = getI18n();

    if (typeof window.showConfirmDialog !== "function") {
        window.alert(message);
        return Promise.resolve();
    }

    setCancelButtonHidden(true);
    return window
        .showConfirmDialog({
            message,
            title: i18n.confirmTitle || gettext("Please confirm"),
            confirmLabel: i18n.okLabel || gettext("OK"),
            cancelLabel: i18n.cancelLabel || gettext("Cancel"),
            confirmClass: "btn-primary",
        })
        .finally(() => {
            setCancelButtonHidden(false);
        });
}

function refreshSelectedCount() {
    const checked = document.querySelectorAll(".shift-checkbox:checked").length;
    const badge = document.querySelector("[data-shift-selected-count]");
    const i18n = getI18n();
    if (!badge) {
        return;
    }
    if (checked > 0) {
        badge.textContent = `${checked} ${i18n.selected || gettext("selected")}`;
        badge.hidden = false;
    } else {
        badge.textContent = "";
        badge.hidden = true;
    }
}

function setupSelectAll() {
    const selectAll = document.getElementById("select-all");
    if (!selectAll) {
        return;
    }

    selectAll.addEventListener("change", () => {
        document.querySelectorAll(".shift-checkbox").forEach((checkbox) => {
            checkbox.checked = selectAll.checked;
        });
        refreshSelectedCount();
    });
}

function setupCheckboxes() {
    document.querySelectorAll(".shift-checkbox").forEach((checkbox) => {
        checkbox.addEventListener("change", refreshSelectedCount);
    });
}

function setupBulkDelete() {
    const i18n = getI18n();

    document.querySelectorAll(".teamshifts-bulk-delete-btn").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();

            const selectedCount = document.querySelectorAll(".shift-checkbox:checked").length;
            if (selectedCount === 0) {
                showAlertDialog(i18n.selectOne || gettext("Please select at least one shift."));
                return;
            }

            const confirmMsg = gettext("Are you sure you want to delete the selected shifts?");
            if (typeof window.showConfirmDialog === "function") {
                window
                    .showConfirmDialog({
                        message: confirmMsg,
                        title: gettext("Please confirm"),
                        confirmLabel: gettext("Confirm"),
                        cancelLabel: gettext("Cancel"),
                        confirmClass: "btn-danger",
                    })
                    .then((confirmed) => {
                        if (confirmed) {
                            button.form.requestSubmit(button);
                        }
                    });
            } else if (window.confirm(confirmMsg)) {
                button.form.requestSubmit(button);
            }
        });
    });
}

document.addEventListener("DOMContentLoaded", () => {
    setupSelectAll();
    setupCheckboxes();
    setupBulkDelete();
    refreshSelectedCount();
});

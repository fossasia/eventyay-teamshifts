function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
}

function getConfirmMessage(newStatus, emailStatuses) {
    if (emailStatuses.includes(newStatus)) {
        return gettext("Are you sure you want to change the status? This may send an email to the applicant.");
    }
    return gettext("Are you sure you want to update the application status?");
}

function setupStatusDropdowns() {
    document.querySelectorAll(".teamshifts-status-dropdown").forEach((select) => {
        const currentStatus = select.dataset.currentStatus;
        const emailStatuses = (select.dataset.emailStatuses || "").split(",").filter(Boolean);

        select.addEventListener("change", () => {
            const newStatus = select.value;

            if (newStatus === currentStatus) {
                return;
            }

            window
                .showConfirmDialog({ message: getConfirmMessage(newStatus, emailStatuses) })
                .then((confirmed) => {
                    if (confirmed) {
                        select.form.requestSubmit();
                    } else {
                        select.value = currentStatus;
                    }
                });
        });
    });
}

function setupSelectAll() {
    const selectAll = document.getElementById("select-all");
    if (!selectAll) {
        return;
    }

    selectAll.addEventListener("change", () => {
        document.querySelectorAll(".app-checkbox").forEach((checkbox) => {
            checkbox.checked = selectAll.checked;
        });
    });
}

function getBulkActionConfirmMessage(action) {
    if (action === "accept") {
        return gettext("Are you sure you want to accept the selected applications? This may send emails to the applicants.");
    }
    return gettext("Are you sure you want to reject the selected applications? This may send emails to the applicants.");
}

function setupBulkActions() {
    document.querySelectorAll(".teamshifts-bulk-action-btn").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();

            const selectedCount = document.querySelectorAll(".app-checkbox:checked").length;
            if (selectedCount === 0) {
                window.alert(gettext("Please select at least one application."));
                return;
            }

            window
                .showConfirmDialog({ message: getBulkActionConfirmMessage(button.value) })
                .then((confirmed) => {
                    if (confirmed) {
                        button.form.requestSubmit(button);
                    }
                });
        });
    });
}

function initApplications() {
    setupStatusDropdowns();
    setupSelectAll();
    setupBulkActions();
}

document.addEventListener("DOMContentLoaded", initApplications);
document.addEventListener("eventyay:ajax-results-replaced", initApplications);

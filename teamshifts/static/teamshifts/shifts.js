function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
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
    });
}

function setupBulkDelete() {
    document.querySelectorAll(".teamshifts-bulk-delete-btn").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();

            const selectedCount = document.querySelectorAll(".shift-checkbox:checked").length;
            if (selectedCount === 0) {
                window.alert(gettext("Please select at least one shift."));
                return;
            }

            const confirmMsg = gettext("Are you sure you want to delete the selected shifts?");
            if (typeof window.showConfirmDialog === "function") {
                window
                    .showConfirmDialog({ message: confirmMsg })
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
    setupBulkDelete();
});

import { setupAjaxSorting } from "./list_sorting.js";

function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
}

setupAjaxSorting(() => {});
document.addEventListener("DOMContentLoaded", () => {
    const selectAll = document.getElementById("select-all-rooms");
    if (!selectAll) return;

    const checkboxes = document.querySelectorAll(".room-checkbox");
    if (checkboxes.length === 0) return;

    const countLabel = document.getElementById("rooms-selected-count");

    const refreshSelectedCount = () => {
        const count = Array.from(checkboxes).filter((cb) => cb.checked).length;

        if (!countLabel) {
            return;
        }

        const selectedText = countLabel.dataset.selected || gettext("selected");
        countLabel.textContent = count > 0 ? `${count} ${selectedText}` : "";
        countLabel.hidden = count === 0;
    };

    selectAll.addEventListener("change", () => {
        checkboxes.forEach((cb) => {
            cb.checked = selectAll.checked;
        });

        refreshSelectedCount();
    });

    checkboxes.forEach((cb) => {
        cb.addEventListener("change", () => {
            selectAll.checked = Array.from(checkboxes).every((c) => c.checked);
            refreshSelectedCount();
        });
    });

    refreshSelectedCount();
});

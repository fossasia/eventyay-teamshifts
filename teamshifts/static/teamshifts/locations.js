document.addEventListener("DOMContentLoaded", () => {
    const selectAll = document.getElementById("select-all-rooms");
    if (!selectAll) return;

    const checkboxes = document.querySelectorAll(".room-checkbox");
    if (checkboxes.length === 0) return;

    selectAll.addEventListener("change", () => {
        checkboxes.forEach((cb) => {
            cb.checked = selectAll.checked;
        });
    });

    checkboxes.forEach((cb) => {
        cb.addEventListener("change", () => {
            selectAll.checked = Array.from(checkboxes).every((c) => c.checked);
        });
    });
});

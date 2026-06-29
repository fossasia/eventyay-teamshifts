const ROLE_SELECT_ID = "id_role";
const ROW_SELECTOR = ".teamshifts-question-row";

function syncQuestionVisibility() {
    const roleSelect = document.getElementById(ROLE_SELECT_ID);
    if (!roleSelect) return;
    const selectedRole = roleSelect.value || "";
    document.querySelectorAll(ROW_SELECTOR).forEach((row) => {
        const questionRole = row.dataset.questionRole || "";
        const visible = questionRole === "" || questionRole === selectedRole;
        row.hidden = !visible;
        // Disable hidden inputs so they don't submit and don't trip required validation.
        row.querySelectorAll("input, select, textarea").forEach((el) => {
            el.disabled = !visible;
        });
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const roleSelect = document.getElementById(ROLE_SELECT_ID);
    if (!roleSelect) return;
    roleSelect.addEventListener("change", syncQuestionVisibility);
    syncQuestionVisibility();
});

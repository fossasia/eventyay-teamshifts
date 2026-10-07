function fieldValues(form) {
    return Array.from(form.querySelectorAll("input[name], select[name]"))
        .filter((field) => field.name !== "csrfmiddlewaretoken")
        .map((field) => (field.type === "checkbox" ? field.checked : field.value));
}

function initSendVouchersButton() {
    const form = document.getElementById("voucher-settings-form");
    const button = document.getElementById("send-vouchers-btn");
    const hint = document.getElementById("send-vouchers-hint");
    const unsavedHint = document.getElementById("send-vouchers-unsaved-hint");
    if (!form || !button || !hint || !unsavedHint) return;

    const savedValues = JSON.stringify(fieldValues(form));
    const savedDisabled = button.classList.contains("disabled");
    const savedHintHidden = hint.hidden;

    function update() {
        const dirty = JSON.stringify(fieldValues(form)) !== savedValues;
        const disabled = dirty || savedDisabled;
        button.classList.toggle("disabled", disabled);
        if (disabled) {
            button.removeAttribute("href");
            button.setAttribute("aria-disabled", "true");
            button.setAttribute("tabindex", "-1");
        } else {
            button.setAttribute("href", button.dataset.href);
            button.removeAttribute("aria-disabled");
            button.removeAttribute("tabindex");
        }
        unsavedHint.hidden = !dirty;
        hint.hidden = dirty || savedHintHidden;
    }

    form.addEventListener("input", update);
    form.addEventListener("change", update);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSendVouchersButton);
} else {
    initSendVouchersButton();
}

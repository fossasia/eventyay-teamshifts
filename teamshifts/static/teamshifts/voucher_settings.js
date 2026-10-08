function initSendVouchersButton() {
    const form = document.getElementById("voucher-settings-form");
    const button = document.getElementById("send-vouchers-btn");
    const hint = document.getElementById("send-vouchers-hint");
    const unsavedHint = document.getElementById("send-vouchers-unsaved-hint");
    const enabledField = form && form.querySelector("[name='enabled']");
    const tagField = form && form.querySelector("[name='voucher_tag']");
    if (!form || !button || !hint || !unsavedHint || !enabledField || !tagField) return;

    // Compare against the persisted settings, not the rendered form: after an
    // invalid POST the form shows the rejected values, which are not saved.
    const savedEnabled = form.dataset.savedEnabled === "true";
    const savedTag = form.dataset.savedVoucherTag || "";
    const blocked = button.dataset.blocked === "true";

    function update() {
        const dirty = enabledField.checked !== savedEnabled || tagField.value !== savedTag;
        const disabled = dirty || blocked;
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
        hint.hidden = dirty || !blocked;
    }

    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSendVouchersButton);
} else {
    initSendVouchersButton();
}

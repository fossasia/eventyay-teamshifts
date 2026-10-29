const picker = document.querySelector(".custom-template-picker");

if (picker) {
    picker.addEventListener("submit", (event) => {
        const template = picker.querySelector('select[name="template"]');

        if (!template?.value) {
            event.preventDefault();
            template?.focus();
            return;
        }
        const status = document.querySelector("#id_status");
        const sendAfter = document.querySelector("#id_send_after");

        picker.querySelector("#template_picker_status").value =
            status?.value ?? "";
        picker.querySelector("#template_picker_send_after").value =
            sendAfter?.value ?? "";
    });
}

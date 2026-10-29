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

        picker.querySelector("#template_picker_submitted").value = "1";

        const volunteers = document.querySelector("#id_individual_volunteers");
        for (const option of volunteers?.selectedOptions ?? []) {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = "individual_volunteers";
            input.value = option.value;
            picker.append(input);
        }
    });
}

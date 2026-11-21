const button = document.querySelector("#load-email-template");

if (button) {
    const templates = JSON.parse(
        document.querySelector("#custom-email-templates").textContent
    );

    button.addEventListener("click", () => {
        const select = document.querySelector("#id_template_picker");
        const template = templates.find(
            (item) => String(item.id) === select.value
        );

        if (!template) return;

        document.querySelectorAll('input[name^="subject_"]').forEach((input) => {
            input.value = template.subject[input.lang] ?? "";
        });

        document.querySelectorAll('textarea[name^="message_"]').forEach((textarea) => {
            const content = template.body[textarea.lang] ?? "";
            const editor = textarea.__eventyayTiptapEditor;

            if (editor) {
                editor.commands.setContent(content);
            } else {
                textarea.value = content;
            }
        });
    });
}

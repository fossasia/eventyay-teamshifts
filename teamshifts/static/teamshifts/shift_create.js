import { bindRemoveRowHandler, markFormsetJsEnabled, setupDynamicFormset } from "./shift_role_formset.js";

function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
}

function setupModeToggle() {
    const modeRadios = document.querySelectorAll('input[name="mode"]');
    const repeatOptionsDiv = document.getElementById("repeat-options");
    if (!modeRadios.length || !repeatOptionsDiv) {
        return;
    }

    function toggleRepeatOptions() {
        let isRepeating = false;
        modeRadios.forEach((radio) => {
            if (radio.checked && radio.value === "repeating") {
                isRepeating = true;
            }
        });
        repeatOptionsDiv.style.display = isRepeating ? "block" : "none";
    }

    modeRadios.forEach((radio) => radio.addEventListener("change", toggleRepeatOptions));
    toggleRepeatOptions();
}

function showPreviewMessage(container, cssClass, text) {
    const p = document.createElement("p");
    p.className = cssClass;
    p.textContent = text;
    container.replaceChildren(p);
    container.style.display = "block";
}

function setupPreviewOccurrences() {
    const previewBtn = document.getElementById("preview-occurrences-btn");
    const previewDiv = document.getElementById("occurrences-preview");
    const startTimeInput = document.getElementById("id_start_time");
    const endTimeInput = document.getElementById("id_end_time");
    const lengthInput = document.getElementById("id_shift_length_minutes");

    if (!previewBtn || !previewDiv || !startTimeInput || !endTimeInput || !lengthInput) {
        return;
    }

    function formatDt(dt) {
        const year = dt.getFullYear();
        const month = String(dt.getMonth() + 1).padStart(2, "0");
        const day = String(dt.getDate()).padStart(2, "0");
        const hrs = String(dt.getHours()).padStart(2, "0");
        const mins = String(dt.getMinutes()).padStart(2, "0");
        return `${year}-${month}-${day} ${hrs}:${mins}`;
    }

    previewBtn.addEventListener("click", () => {
        const startStr = startTimeInput.value;
        const endStr = endTimeInput.value;
        const lengthStr = lengthInput.value;

        if (!startStr || !endStr || !lengthStr) {
            showPreviewMessage(previewDiv, "text-danger", gettext("Please fill in Start Time, End Time, and Shift Length."));
            return;
        }

        const startDate = new Date(startStr);
        const endDate = new Date(endStr);
        const lengthMins = parseInt(lengthStr, 10);

        if (endDate <= startDate) {
            showPreviewMessage(previewDiv, "text-danger", gettext("End Time must be after Start Time."));
            return;
        }

        if (lengthMins <= 0) {
            showPreviewMessage(previewDiv, "text-danger", gettext("Shift Length must be greater than 0."));
            return;
        }

        if (startDate.getTime() + lengthMins * 60000 > endDate.getTime()) {
            showPreviewMessage(previewDiv, "text-danger", gettext("The shift length is longer than the time between start and end."));
            return;
        }

        const fragment = document.createDocumentFragment();

        let curr = new Date(startDate);
        const occurrences = [];
        const maxShifts = 50;

        while (curr < endDate && occurrences.length < maxShifts) {
            const next = new Date(Math.min(curr.getTime() + lengthMins * 60000, endDate.getTime()));
            const mins = Math.round((next - curr) / 60000);
            let text = `${formatDt(curr)} \u2013 ${formatDt(next)}`;
            if (mins < lengthMins) {
                text += ` (${gettext("%(minutes)s min, shorter than the others").replace("%(minutes)s", mins)})`;
            }
            occurrences.push(text);
            curr = next;
        }

        if (curr < endDate) {
            showPreviewMessage(previewDiv, "text-danger", gettext("The maximum allowed is 50 per action. Please adjust the interval or date range."));
            return;
        }

        const summary = document.createElement("strong");
        summary.textContent = `${gettext("Will create")} ${occurrences.length} ${gettext("shifts:")}`;
        fragment.appendChild(summary);
        occurrences.forEach((text, i) => {
            fragment.appendChild(document.createElement("br"));
            fragment.appendChild(document.createTextNode(text));
        });

        previewDiv.replaceChildren(fragment);
        previewDiv.style.display = "block";
    });
}

document.addEventListener("DOMContentLoaded", () => {
    setupModeToggle();
    setupPreviewOccurrences();
    setupDynamicFormset();
    bindRemoveRowHandler();
    markFormsetJsEnabled();
});

function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
}

/**
 * @throws {Error} when the request fails (network error or non-2xx response)
 */
async function toggleArrived(form) {
    const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: {
            "X-Requested-With": "XMLHttpRequest",
        },
    });
    if (!response.ok) {
        throw new Error(`Toggle arrived request failed with status ${response.status}`);
    }
    return response.json();
}

function setButtonState(button, arrived) {
    button.className = arrived ? "btn btn-sm btn-success" : "btn btn-sm btn-default";
    const icon = document.createElement("i");
    icon.className = arrived ? "fa fa-check" : "fa fa-times";
    const label = document.createTextNode(` ${gettext(arrived ? "Arrived" : "Not arrived")}`);
    button.replaceChildren(icon, label);
}

function setButtonLoading(button) {
    button.replaceChildren();
    const icon = document.createElement("i");
    icon.className = "fa fa-spinner fa-spin";
    button.appendChild(icon);
}

function restoreButtonChildren(button, originalChildren) {
    button.replaceChildren(...originalChildren);
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".toggle-arrived-form").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const button = form.querySelector("button");
            const originalChildren = Array.from(button.childNodes).map((node) => node.cloneNode(true));
            button.disabled = true;
            setButtonLoading(button);

            try {
                const data = await toggleArrived(form);
                if (data.success) {
                    setButtonState(button, data.arrived);
                } else {
                    restoreButtonChildren(button, originalChildren);
                    alert(gettext("An error occurred."));
                }
            } catch (error) {
                console.error("Failed to toggle arrived status", error);
                restoreButtonChildren(button, originalChildren);
                alert(gettext("An error occurred."));
            } finally {
                button.disabled = false;
            }
        });
    });
});


function updateBulkActionBar() {
    const bar = document.getElementById("bulk-action-bar");
    const countEl = document.getElementById("selected-count");
    if (!bar || !countEl) return;

    const checked = document.querySelectorAll(".member-checkbox:checked");
    countEl.textContent = checked.length;
    bar.style.display = checked.length > 0 ? "" : "none";
}

document.addEventListener("DOMContentLoaded", () => {
    const selectAll = document.getElementById("select-all");
    const checkboxes = document.querySelectorAll(".member-checkbox");

    if (!selectAll || checkboxes.length === 0) return;

    selectAll.addEventListener("change", () => {
        checkboxes.forEach((cb) => {
            cb.checked = selectAll.checked;
        });
        updateBulkActionBar();
    });

    checkboxes.forEach((cb) => {
        cb.addEventListener("change", () => {
            const allChecked = Array.from(checkboxes).every((c) => c.checked);
            selectAll.checked = allChecked;
            updateBulkActionBar();
        });
    });
});

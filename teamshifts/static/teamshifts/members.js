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

function submitBulkVouchers() {
    const form = document.getElementById("bulk-voucher-form");
    if (!form) return;

    // Clear any previously injected hidden inputs
    form.querySelectorAll('input[name="member_ids"]').forEach((el) => el.remove());

    const checked = document.querySelectorAll(".member-checkbox:checked");
    if (checked.length === 0) {
        alert(gettext("Select at least one member."));
        return;
    }

    checked.forEach((cb) => {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "member_ids";
        hidden.value = cb.dataset.memberId;
        form.appendChild(hidden);
    });

    form.submit();
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

    const selectAll = document.getElementById("select-all");
    const checkboxes = document.querySelectorAll(".member-checkbox");

    if (selectAll && checkboxes.length > 0) {
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
    }

    // Bulk send button
    const sendBtn = document.getElementById("bulk-send-btn");
    if (sendBtn) {
        sendBtn.addEventListener("click", submitBulkVouchers);
    }
});

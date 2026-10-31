function gettext(msgid) {
    return typeof window.gettext === "function" ? window.gettext(msgid) : msgid;
}

function ngettext(singular, plural, count) {
    return typeof window.ngettext === "function" ? window.ngettext(singular, plural, count) : count === 1 ? singular : plural;
}

function withCount(template, count) {
    return template.replace("%(count)s", String(count));
}

function setupRemoveMembers(checkboxes) {
    const button = document.getElementById("bulk-remove-btn");
    const dialog = document.getElementById("remove-members-dialog");
    const form = document.getElementById("remove-members-form");
    if (!button || !dialog || !form) return;

    const countLabel = document.getElementById("members-selected-count");
    const selected = () => Array.from(checkboxes).filter((cb) => cb.checked);

    const refreshButton = () => {
        const count = selected().length;
        button.disabled = count === 0;
        if (countLabel) {
            countLabel.textContent = count > 0 ? `${count} ${gettext("selected")}` : "";
            countLabel.hidden = count === 0;
        }
    };
    checkboxes.forEach((cb) => cb.addEventListener("change", refreshButton));
    document.getElementById("select-all")?.addEventListener("change", refreshButton);
    refreshButton();

    button.addEventListener("click", () => {
        const members = selected();
        if (members.length === 0) return;

        form.querySelectorAll('input[name="member_ids"]').forEach((el) => el.remove());
        const list = document.getElementById("remove-members-list");
        list.replaceChildren();
        let freedSpots = 0;
        members.forEach((cb) => {
            const upcoming = Number.parseInt(cb.dataset.upcomingShifts, 10) || 0;
            freedSpots += upcoming;
            const item = document.createElement("li");
            const name = document.createElement("strong");
            name.textContent = cb.dataset.memberName;
            const shifts = withCount(ngettext("%(count)s upcoming shift", "%(count)s upcoming shifts", upcoming), upcoming);
            item.append(name, ` · ${shifts}`);
            list.appendChild(item);

            const hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = "member_ids";
            hidden.value = cb.dataset.memberId;
            form.appendChild(hidden);
        });

        const count = members.length;
        document.getElementById("remove-members-title").textContent = withCount(
            ngettext("Remove %(count)s member from the team?", "Remove %(count)s members from the team?", count),
            count,
        );
        document.getElementById("remove-members-confirm").textContent = withCount(
            ngettext("Remove %(count)s member", "Remove %(count)s members", count),
            count,
        );
        document.getElementById("remove-members-spots").textContent = withCount(
            ngettext(
                "%(count)s upcoming shift spot will be freed up. Shifts that have already started or ended stay in their history.",
                "%(count)s upcoming shift spots will be freed up. Shifts that have already started or ended stay in their history.",
                freedSpots,
            ),
            freedSpots,
        );
        dialog.showModal();
    });

    document.getElementById("remove-members-cancel").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", () => {
        document.getElementById("remove-members-confirm").disabled = true;
    });
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) {
            document.getElementById("remove-members-confirm").disabled = false;
            refreshButton();
        }
    });
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

function submitBulkForm(formId, button) {
    const form = document.getElementById(formId);
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

    button.disabled = true;
    form.requestSubmit();
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

    setupRemoveMembers(checkboxes);

    // Bulk send button
    const sendBtn = document.getElementById("bulk-send-btn");
    if (sendBtn) {
        sendBtn.addEventListener("click", () => submitBulkForm("bulk-voucher-form", sendBtn));
    }

    const sendCertificatesBtn = document.getElementById("bulk-send-certificates-btn");
    if (sendCertificatesBtn) {
        sendCertificatesBtn.addEventListener("click", () => submitBulkForm("bulk-certificate-form", sendCertificatesBtn));
    }

    window.addEventListener("pageshow", (event) => {
        if (event.persisted) {
            [sendBtn, sendCertificatesBtn].forEach((button) => {
                if (button) button.disabled = false;
            });
        }
    });
});

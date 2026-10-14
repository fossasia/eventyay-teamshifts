
export function setupAjaxSorting(onUpdate) {
    document.addEventListener("click", async (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }

        const link = event.target.closest("a[data-ajax]");
        if (!link || !link.closest("[data-teamshifts-list]")) {
            return;
        }

        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }

        event.preventDefault();

        try {
            const response = await fetch(link.href, {
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
            });

            if (!response.ok) {
                throw new Error("Unable to update the list");
            }

            const html = await response.text();
            const parsed = new DOMParser().parseFromString(html, "text/html");
            const updatedList = parsed.querySelector("[data-teamshifts-list]");
            const currentList = document.querySelector("[data-teamshifts-list]");

            if (!updatedList || !currentList) {
                window.location.assign(link.href);
                return;
            }

            currentList.innerHTML = updatedList.innerHTML;
            window.history.pushState({}, "", link.href);
            onUpdate();
        } catch {
            window.location.assign(link.href);
        }
    });

    window.addEventListener("popstate", () => {
        window.location.reload();
    });
}

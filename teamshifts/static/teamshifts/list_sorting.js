
export function setupAjaxSorting(onUpdate) {
    let latestRequest = 0;

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
        const requestId = ++latestRequest;

        try {
            const response = await fetch(link.href, {
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
            });

            if (requestId !== latestRequest) return;

            if (!response.ok) {
                throw new Error("Unable to update the list");
            }

            const html = await response.text();
            if (requestId !== latestRequest) return;
            const parsed = new DOMParser().parseFromString(html, "text/html");
            const updatedList = parsed.querySelector("[data-teamshifts-list]");
            const currentList = document.querySelector("[data-teamshifts-list]");

            if (!updatedList || !currentList) {
                window.location.assign(link.href);
                return;
            }

            currentList.replaceWith(updatedList);
            window.history.pushState({}, "", link.href);
            onUpdate();
        } catch {
            if (requestId !== latestRequest) return;
            window.location.assign(link.href);
        }
    });

    window.addEventListener("popstate", () => {
        window.location.reload();
    });
}

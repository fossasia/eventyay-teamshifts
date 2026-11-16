export function setupAjaxSorting(onUpdate) {
    let latestRequest = 0;

    async function updateList(url, pushHistory = false, fallback = () => window.location.reload()) {
        const requestId = ++latestRequest;
        const currentList = document.querySelector("[data-teamshifts-list]");

        if (currentList) {
            currentList.setAttribute("aria-busy", "true");
            currentList.classList.add("opacity-50", "pe-none");
        }

        try {
            const response = await fetch(url, {
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
            });

            if (requestId !== latestRequest) return;
            if (!response.ok) throw new Error("Unable to update the list");

            const html = await response.text();
            if (requestId !== latestRequest) return;

            const parsed = new DOMParser().parseFromString(html, "text/html");
            const updatedList = parsed.querySelector("[data-teamshifts-list]");

            if (!updatedList || !currentList) {
                fallback();
                return;
            }

            const activeElement = document.activeElement;
            const restoreFocus =
                activeElement instanceof Element && currentList.contains(activeElement);
            const linkLabel = restoreFocus ? activeElement.getAttribute("aria-label") : null;

            currentList.replaceWith(updatedList);

            if (restoreFocus && linkLabel) {
                const replacementLink = Array.from(
                    updatedList.querySelectorAll("a[data-ajax]"),
                ).find((candidate) => candidate.getAttribute("aria-label") === linkLabel);

                replacementLink?.focus();
            }

            if (pushHistory) {
                window.history.pushState({}, "", url);
            }

            onUpdate();
        } catch {
            if (requestId === latestRequest) fallback();
        } finally {
            if (requestId === latestRequest) {
                const list = document.querySelector("[data-teamshifts-list]");
                list?.removeAttribute("aria-busy");
                list?.classList.remove("opacity-50", "pe-none");
            }
        }
    }

    document.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) return;

        const link = event.target.closest("a[data-ajax]");
        if (!link || !link.closest("[data-teamshifts-list]")) return;

        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

        event.preventDefault();
        updateList(link.href, true, () => window.location.assign(link.href));
    });

    window.addEventListener("popstate", () => {
        updateList(window.location.href);
    });
}

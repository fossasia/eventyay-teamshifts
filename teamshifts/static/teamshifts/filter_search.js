/**
 * Debounced live search for TeamShifts filter bars.
 *
 * Eventyay core's ``ajax-filter.js`` (loaded by ``pretixcontrol/base.html``)
 * intercepts ``form.filter-form`` submissions, ``.btn-clear-filter`` clicks,
 * pagination links, sorting links, and ``popstate`` navigation. This module
 * complements it by triggering ``form.requestSubmit()`` as the user types in
 * the search box so live search updates the results region via AJAX as well.
 */
const SEARCH_DEBOUNCE_MS = 300;
const FILTER_FORM_SELECTOR = "[data-advanced-filter-search] form.filter-form";

const pendingTimers = new WeakMap();
const lastSubmittedQueries = new WeakMap();

function getQueryInput(form) {
    return form ? form.querySelector('input[name="query"]') : null;
}

function clearPendingTimer(form) {
    const timer = pendingTimers.get(form);
    if (timer !== undefined) {
        clearTimeout(timer);
        pendingTimers.delete(form);
    }
}

function submitFilterForm(form) {
    if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
    } else {
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
}

function scheduleSearch(form, input) {
    clearPendingTimer(form);
    const timer = setTimeout(() => {
        pendingTimers.delete(form);
        const currentValue = input.value;
        if (lastSubmittedQueries.get(form) === currentValue) {
            return;
        }
        lastSubmittedQueries.set(form, currentValue);
        submitFilterForm(form);
    }, SEARCH_DEBOUNCE_MS);
    pendingTimers.set(form, timer);
}

function handleSearchInput(event) {
    if (event.isComposing) {
        return;
    }
    const input = event.target.closest(`${FILTER_FORM_SELECTOR} input[name="query"]`);
    const form = input ? input.closest("form") : null;
    if (form) {
        scheduleSearch(form, input);
    }
}

function handleFormSubmit(event) {
    const form = event.target.closest(FILTER_FORM_SELECTOR);
    if (!form) {
        return;
    }
    clearPendingTimer(form);
    const input = getQueryInput(form);
    if (input) {
        lastSubmittedQueries.set(form, input.value);
    }
}

function handleClearClick(event) {
    const clearButton = event.target.closest(`${FILTER_FORM_SELECTOR} .btn-clear-filter`);
    if (!clearButton) {
        return;
    }
    const form = clearButton.closest("form");
    if (!form) {
        return;
    }
    clearPendingTimer(form);
    lastSubmittedQueries.set(form, "");
}

function handlePopState() {
    const queryValue = new URLSearchParams(window.location.search).get("query") ?? "";
    document.querySelectorAll(FILTER_FORM_SELECTOR).forEach((form) => {
        clearPendingTimer(form);
        const input = getQueryInput(form);
        if (input) {
            input.value = queryValue;
            lastSubmittedQueries.set(form, queryValue);
        }
    });
}

function initFilterSearch() {
    const forms = document.querySelectorAll(FILTER_FORM_SELECTOR);
    if (!forms.length) {
        return;
    }
    forms.forEach((form) => {
        const input = getQueryInput(form);
        if (input) {
            lastSubmittedQueries.set(form, input.value);
        }
    });
    document.addEventListener("input", handleSearchInput);
    document.addEventListener("compositionend", handleSearchInput);
    document.addEventListener("submit", handleFormSubmit);
    document.addEventListener("click", handleClearClick);
    window.addEventListener("popstate", handlePopState);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFilterSearch, { once: true });
} else {
    initFilterSearch();
}

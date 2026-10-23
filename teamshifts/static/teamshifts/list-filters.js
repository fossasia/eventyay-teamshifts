(function () {
    function syncFilterCount() {
        var region = document.querySelector('[data-ajax-results-region]');
        var badge = document.querySelector('[data-filter-count-badge]');
        if (!region || !badge) {
            return;
        }
        var count = parseInt(region.dataset.filterCount, 10) || 0;
        badge.textContent = count ? String(count) : '';
        badge.hidden = count === 0;
    }

    function init() {
        syncFilterCount();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    document.addEventListener('eventyay:ajax-results-replaced', init);
})();

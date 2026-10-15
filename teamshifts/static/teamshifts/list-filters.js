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

    function initToggles() {
        var toggles = document.querySelectorAll('[data-advanced-filter-toggle]');
        toggles.forEach(function (toggle) {
            if (toggle.dataset.teamshiftsBound === '1') {
                return;
            }
            toggle.dataset.teamshiftsBound = '1';
            toggle.addEventListener('click', function () {
                var searchRoot = toggle.closest('[data-advanced-filter-search]');
                if (!searchRoot) return;
                var panel = searchRoot.querySelector('[data-advanced-filter-advanced]');
                if (!panel) return;
                var expanded = toggle.getAttribute('aria-expanded') === 'true';
                toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
                panel.hidden = expanded;
                panel.classList.toggle('is-collapsed', expanded);
            });
        });
    }

    function init() {
        syncFilterCount();
        initToggles();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    document.addEventListener('eventyay:ajax-results-replaced', init);
})();

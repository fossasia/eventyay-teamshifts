document.addEventListener("DOMContentLoaded", () => {
  const picker = document.querySelector("[data-compose-volunteers]");
  const status = document.querySelector("[data-compose-status]");
  if (!picker || !status) return;

  const sync = () => {
    const overridden = picker.selectedOptions.length > 0;
    status.classList.toggle("is-overridden", overridden);
    // inert blocks clicks and focus but, unlike disabled, still submits the value.
    status.toggleAttribute("inert", overridden);
  };

  if (window.jQuery) {
    const $picker = window.jQuery(picker);
    $picker.on("change", sync);
    $picker.on("select2:closing", (event) => {
      if (event.params?.args?.originalSelect2Event) event.preventDefault();
    });
    $picker.on("select2:open", () => {
      const list = document.getElementById(`select2-${picker.id}-results`);
      if (!list || list.dataset.tsPickedRowsRemoved) return;
      list.dataset.tsPickedRowsRemoved = "1";
      new MutationObserver(() => {
        list.querySelectorAll('.select2-results__option[aria-selected="true"]').forEach((row) => row.remove());
        if (!list.querySelector(".select2-results__option--highlighted")) {
          const first = list.querySelector('.select2-results__option[aria-selected="false"]');
          if (first) window.jQuery(first).trigger("mouseenter");
        }
      }).observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-selected"] });
    });
  }
  picker.addEventListener("change", sync);
  sync();
});

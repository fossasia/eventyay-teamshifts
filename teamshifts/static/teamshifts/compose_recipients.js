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

  if (window.jQuery) window.jQuery(picker).on("change", sync);
  picker.addEventListener("change", sync);
  sync();
});

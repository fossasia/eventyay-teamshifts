document.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("ts-self-signup-btn");
  const dialog = document.getElementById("ts-self-signup-dialog");
  if (!button || !dialog) return;

  button.addEventListener("click", () => dialog.showModal());
  document.getElementById("ts-self-signup-cancel")?.addEventListener("click", () => dialog.close());
});

function renderButton(button, iconClass, label) {
  while (button.firstChild) {
    button.removeChild(button.firstChild);
  }
  const icon = document.createElement("i");
  icon.className = iconClass;
  button.appendChild(icon);
  button.appendChild(document.createTextNode(` ${label}`));
}

function initCopyButton(button) {
  const input = document.querySelector(button.dataset.copyTarget);
  if (!input) return;
  const originalIcon = button.querySelector("i");
  const originalIconClass = originalIcon ? originalIcon.className : "fa fa-copy";
  const originalLabel = button.textContent.trim();

  const done = () => {
    renderButton(button, "fa fa-check", button.dataset.copiedLabel || originalLabel);
    window.setTimeout(() => renderButton(button, originalIconClass, originalLabel), 1500);
  };
  const fallback = () => {
    input.select();
    input.setSelectionRange(0, input.value.length);
    document.execCommand("copy");
    done();
  };

  button.addEventListener("click", () => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(input.value).then(done, fallback);
    } else {
      fallback();
    }
  });
}

function initConfirmButton(button) {
  button.addEventListener("click", (event) => {
    if (!window.confirm(button.dataset.confirm)) {
      event.preventDefault();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-copy-target]").forEach(initCopyButton);
  document.querySelectorAll(".my-shifts-calendar-reset [data-confirm]").forEach(initConfirmButton);
});

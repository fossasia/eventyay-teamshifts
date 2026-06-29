document.addEventListener('DOMContentLoaded', function () {
  var activeEl = document.getElementById('id_active');
  var showOnMenuEl = document.getElementById('id_show_on_menu');
  if (!activeEl || !showOnMenuEl) return;

  function updateShowOnMenu() {
    var row = showOnMenuEl.closest('.form-group') || showOnMenuEl.parentElement;
    if (!activeEl.checked) {
      showOnMenuEl.disabled = true;
      showOnMenuEl.checked = false;
      if (row) row.style.opacity = '0.5';
    } else {
      showOnMenuEl.disabled = false;
      if (row) row.style.opacity = '';
    }
  }

  activeEl.addEventListener('change', updateShowOnMenu);
  updateShowOnMenu();
});

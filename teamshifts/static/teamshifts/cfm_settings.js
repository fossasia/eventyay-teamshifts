function getCookie(name) {
  var cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    var cookies = document.cookie.split(';');
    for (var index = 0; index < cookies.length; index++) {
      var cookie = cookies[index].trim();
      if (cookie.substring(0, name.length + 1) === name + '=') {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}

function initDescriptionPreview() {
  var panel = document.getElementById('description_panel');
  if (!panel) return;

  var previewPane = panel.querySelector('#description_preview');
  var previewTab = panel.querySelector('[data-description-preview-tab]');
  if (!previewPane || !previewTab) return;

  var previewUrl = previewPane.dataset.previewUrl;
  var blocks = previewPane.querySelectorAll('.description-preview');
  if (!previewUrl || !blocks.length) return;

  function renderPreview() {
    var inputs = panel.querySelectorAll(
      '#description_edit textarea, #description_edit input[type="text"]'
    );
    var params = new URLSearchParams();
    inputs.forEach(function (input) {
      params.append(input.name, input.value);
    });

    fetch(previewUrl, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCookie('eventyay_csrftoken') || getCookie('csrftoken'),
      },
      credentials: 'include',
      body: params,
    })
      .then(function (response) {
        if (!response.ok) throw new Error('Could not render description preview.');
        return response.json();
      })
      .then(function (data) {
        var msgs = data.msgs || {};
        blocks.forEach(function (block) {
          block.innerHTML = msgs[block.getAttribute('lang')] || '';
        });
      })
      .catch(function () {
        blocks.forEach(function (block) {
          block.textContent = '';
        });
        blocks[0].textContent = gettext('The preview could not be loaded. Please try again.');
      });
  }

  $(previewTab).on('shown.bs.tab', renderPreview);
}

document.addEventListener('DOMContentLoaded', function () {
  initDescriptionPreview();
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

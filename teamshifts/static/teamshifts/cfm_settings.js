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
        var parser = new DOMParser();
        blocks.forEach(function (block) {
          var html = msgs[block.getAttribute('lang')] || '';
          var doc = parser.parseFromString(html, 'text/html');
          while (block.firstChild) {
            block.removeChild(block.firstChild);
          }
          Array.prototype.forEach.call(doc.body.childNodes, function (node) {
            block.appendChild(document.adoptNode(node));
          });
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
  initSecretLinkCopy();
  initCallVisibilityToggles();
  initRegenerateConfirm();
});

function initCallVisibilityToggles() {
  var activeEl = document.getElementById('id_active');
  var showOnMenuEl = document.getElementById('id_show_on_menu');
  var privateEl = document.getElementById('id_cfm_private');
  if (!activeEl || !showOnMenuEl) return;

  // Hidden input preserves the value when the checkbox is disabled.
  var hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.name = showOnMenuEl.name;
  hidden.value = showOnMenuEl.checked ? 'on' : '';
  showOnMenuEl.parentNode.insertBefore(hidden, showOnMenuEl);

  showOnMenuEl.addEventListener('change', function () {
    hidden.value = showOnMenuEl.checked ? 'on' : '';
  });

  function updateShowOnMenu() {
    var row = showOnMenuEl.closest('.form-group') || showOnMenuEl.parentElement;
    var hasEffect = activeEl.checked && !(privateEl && privateEl.checked);
    if (!hasEffect) {
      showOnMenuEl.disabled = true;
      if (row) row.style.opacity = '0.5';
    } else {
      showOnMenuEl.disabled = false;
      if (row) row.style.opacity = '';
    }
  }

  activeEl.addEventListener('change', updateShowOnMenu);
  if (privateEl) privateEl.addEventListener('change', updateShowOnMenu);
  updateShowOnMenu();
}

function initSecretLinkCopy() {
  var button = document.getElementById('cfm-copy-secret-link');
  var input = document.getElementById('cfm-secret-link-input');
  if (!button || !input) return;

  var originalIcon = button.querySelector('i');
  var originalIconClass = originalIcon ? originalIcon.className : 'fa fa-copy';
  var originalLabel = button.textContent.trim();

  function renderButton(iconClass, label) {
    while (button.firstChild) {
      button.removeChild(button.firstChild);
    }
    var icon = document.createElement('i');
    icon.className = iconClass;
    button.appendChild(icon);
    button.appendChild(document.createTextNode(' ' + label));
  }

  button.addEventListener('click', function () {
    input.select();
    input.setSelectionRange(0, input.value.length);
    var done = function () {
      renderButton('fa fa-check', button.dataset.copiedLabel || gettext('Copied'));
      window.setTimeout(function () {
        renderButton(originalIconClass, originalLabel);
      }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(input.value).then(done, function () {
        document.execCommand('copy');
        done();
      });
    } else {
      document.execCommand('copy');
      done();
    }
  });
}

function initRegenerateConfirm() {
  var btn = document.querySelector('.call-secret-regenerate [data-confirm]');
  if (!btn) return;
  btn.addEventListener('click', function (e) {
    var msg = btn.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
    }
  });
}

document.addEventListener('DOMContentLoaded', function () {
  var form = document.querySelector('form[data-preview-url]')
  if (!form) return

  var previewUrl = form.getAttribute('data-preview-url')

  function getCsrfToken() {
    var input = form.querySelector('input[name="csrfmiddlewaretoken"]')
    return input ? input.value : ''
  }

  // Tiptap fills its hidden textarea without firing an event, so Are-You-Sure must re-check before Reset leaves the page.
  form.addEventListener('click', function (event) {
    if (window.jQuery && event.target.closest('[data-template-reset]')) {
      window.jQuery(form).trigger('checkform.areYouSure')
    }
  })

  form.querySelectorAll('.email-template-panel').forEach(function (panel) {
    var previewTab = panel.querySelector('[data-template-preview-tab]')
    var previewGroup = panel.querySelector('.mail-preview-group')
    if (!previewTab || !previewGroup) return

    var blocks = previewGroup.querySelectorAll('.mail-preview')

    function renderPreview() {
      var params = new URLSearchParams()
      panel.querySelectorAll('textarea[name*="body"]').forEach(function (textarea) {
        params.append('body', textarea.value)
      })

      fetch(previewUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCsrfToken(),
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        credentials: 'same-origin',
        body: params,
      })
        .then(function (response) {
          if (!response.ok) throw new Error('preview failed')
          return response.json()
        })
        .then(function (data) {
          var previews = data.previews || {}
          blocks.forEach(function (block) {
            var locale = block.getAttribute('lang')
            block.innerHTML = previews[locale] || ''
          })
        })
        .catch(function () {
          blocks.forEach(function (block) {
            block.textContent = (typeof window.gettext === 'function' ? window.gettext('The preview could not be loaded.') : 'The preview could not be loaded.')
          })
        })
    }

    if (window.jQuery) {
      window.jQuery(previewTab).on('shown.bs.tab', renderPreview)
    }
  })
})

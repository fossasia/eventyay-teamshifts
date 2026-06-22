function tsGetCsrf() {
  const m = document.cookie.match(/eventyay_csrftoken=([^;]+)/);
  return m ? m[1] : '';
}

function tsClearIndicators(tbody) {
  tbody.querySelectorAll('[dragsort-id]').forEach(function (el) {
    el.classList.remove('drag-indicator', 'insert-before', 'insert-after');
  });
}

function tsPushOrder(tbody) {
  const url = tbody.getAttribute('dragsort-url');
  if (!url) return;
  const ids = Array.from(tbody.querySelectorAll('[dragsort-id]'))
    .map(function (el) { return el.getAttribute('dragsort-id'); })
    .filter(Boolean);
  const body = new URLSearchParams();
  body.append('order', ids.join(','));
  fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': tsGetCsrf() },
    body: body,
  });
}

document.addEventListener('DOMContentLoaded', function () {
  document.addEventListener('dragstart', function (evt) {
    const handle = evt.target.closest('.dragsort-button');
    if (!handle) return;
    const row = handle.closest('[dragsort-id]');
    if (!row) return;
    const tbody = row.closest('[dragsort-url]');
    if (!tbody) return;

    evt.dataTransfer.effectAllowed = 'move';
    setTimeout(function () { row.classList.add('dragging'); }, 0);

    const rows = Array.from(tbody.querySelectorAll('[dragsort-id]'));
    let closest = row;
    let intent = 'before';

    function onDragover(e) {
      e.preventDefault();
      let best = null;
      let bestDist = Infinity;
      rows.forEach(function (r) {
        const rect = r.getBoundingClientRect();
        const mid = (rect.top + rect.bottom) / 2;
        const dist = Math.abs(e.clientY - mid);
        if (dist < bestDist) { bestDist = dist; best = r; }
      });
      if (!best) return;
      closest = best;
      const rect = best.getBoundingClientRect();
      const mid = (rect.top + rect.bottom) / 2;
      intent = e.clientY < mid ? 'before' : 'after';
      tsClearIndicators(tbody);
      best.classList.add('drag-indicator', intent === 'before' ? 'insert-before' : 'insert-after');
    }

    tbody.addEventListener('dragover', onDragover);
    tbody.addEventListener('drop', function (e) { e.preventDefault(); }, { once: true });

    document.addEventListener('dragend', function () {
      row.classList.remove('dragging');
      tsClearIndicators(tbody);
      tbody.removeEventListener('dragover', onDragover);
      if (intent === 'after') {
        closest.insertAdjacentElement('afterend', row);
      } else {
        closest.insertAdjacentElement('beforebegin', row);
      }
      tsPushOrder(tbody);
    }, { once: true });
  });
});

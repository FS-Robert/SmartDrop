/* Avisos automáticos de fuga para administradores: campana con contador + tarjetas emergentes. */
(function () {
  const script = document.currentScript;
  const alertsUrl = script.dataset.alertsUrl;
  const leaksUrl = script.dataset.leaksUrl;
  const badge = document.getElementById('leak-bell-badge');
  const STORAGE_KEY = 'smartdrop:lastLeakAlertId';
  const POLL_MS = 20000;
  const MAX_TOASTS = 3;
  let lastShownId = Number(localStorage.getItem(STORAGE_KEY) || 0);

  function container() {
    let box = document.getElementById('leak-toasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'leak-toasts';
      box.className = 'leak-toasts';
      box.setAttribute('role', 'region');
      box.setAttribute('aria-label', 'Avisos de fuga');
      document.body.appendChild(box);
    }
    return box;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function showToast(alert) {
    const box = container();
    while (box.children.length >= MAX_TOASTS) box.removeChild(box.firstChild);
    const metrics = alert.metricas || {};
    const loss = metrics.perdida_estimada_lph != null ? `Pérdida estimada ≈ ${Number(metrics.perdida_estimada_lph).toFixed(1)} L/h` : '';
    const percent = alert.probabilidad != null ? `${Math.round(alert.probabilidad * 100)} %` : '';
    const toast = document.createElement('div');
    toast.className = `leak-toast prioridad-${escapeHtml(alert.prioridad || 'alta')}`;
    toast.innerHTML = `
      <i class="ti ti-alert-triangle leak-toast-icon" aria-hidden="true"></i>
      <div class="leak-toast-body">
        <strong>Posible fuga detectada${percent ? ` (${percent})` : ''}</strong>
        <span>${escapeHtml(alert.nic || '')} · ${escapeHtml(alert.zona || '')}</span>
        <span>${escapeHtml(alert.direccion || '')}</span>
        ${loss ? `<span>${escapeHtml(loss)}</span>` : ''}
        <a href="${leaksUrl}#alerta-${alert.id_alerta}">Ver todos los detalles</a>
      </div>
      <button type="button" class="leak-toast-close" aria-label="Cerrar">&times;</button>`;
    toast.querySelector('.leak-toast-close').addEventListener('click', () => toast.remove());
    box.appendChild(toast);
  }

  async function poll() {
    try {
      const response = await fetch(`${alertsUrl}?solo_no_leidas=1`, {
        credentials: 'same-origin', headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      const data = await response.json();
      const unread = data.no_leidas || 0;
      if (badge) {
        badge.textContent = unread > 99 ? '99+' : String(unread);
        badge.hidden = unread === 0;
      }
      const fresh = (data.alertas || []).filter((a) => a.id_alerta > lastShownId).sort((a, b) => a.id_alerta - b.id_alerta);
      if (fresh.length) {
        fresh.slice(-MAX_TOASTS).forEach(showToast);
        lastShownId = fresh[fresh.length - 1].id_alerta;
        localStorage.setItem(STORAGE_KEY, String(lastShownId));
      }
    } catch (error) {
      // Sin conexión momentánea: se reintenta en el siguiente ciclo.
    }
  }

  window.SmartDropLeakAlerts = { refresh: poll };
  poll();
  window.setInterval(poll, POLL_MS);
}());

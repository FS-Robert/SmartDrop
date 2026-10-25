/* Avisos de fuga del sector para residentes (PB027): llegan por WebSocket y se muestran al instante como
   tarjeta emergente, en "Alertas Activas" del panel y, si el navegador lo permite, como notificación. */
(function () {
  const MAX_TOASTS = 3;

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

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

  function lineaDetalle(mensaje) {
    const lineas = String(mensaje || '').split('\n');
    return lineas[lineas.length - 1];
  }

  function mostrarTarjeta(aviso) {
    const box = container();
    while (box.children.length >= MAX_TOASTS) box.removeChild(box.firstChild);
    const toast = document.createElement('div');
    toast.className = 'leak-toast prioridad-alta';
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
      <i class="ti ti-alert-triangle leak-toast-icon" aria-hidden="true"></i>
      <div class="leak-toast-body">
        <strong>${escapeHtml(aviso.titulo)}</strong>
        <span>${escapeHtml(aviso.zona || '')}</span>
        <span>${escapeHtml(lineaDetalle(aviso.mensaje))}</span>
      </div>
      <button type="button" class="leak-toast-close" aria-label="Cerrar">&times;</button>`;
    toast.querySelector('.leak-toast-close').addEventListener('click', () => toast.remove());
    box.appendChild(toast);
  }

  function agregarAAlertasActivas(aviso) {
    const lista = document.getElementById('alertas-activas-lista');
    if (!lista || (aviso.id_alerta && lista.querySelector(`[data-id-alerta="${aviso.id_alerta}"]`))) return;
    const item = document.createElement('li');
    item.className = 'list-row alerta-activa alerta-activa--nueva';
    if (aviso.id_alerta) item.dataset.idAlerta = aviso.id_alerta;
    item.innerHTML = `
      <span class="icon-chip icon-chip--bad"><i class="ti ti-droplet-off" aria-hidden="true"></i></span>
      <div class="list-row-main">
        <p class="list-row-title">${escapeHtml(aviso.titulo)}</p>
        <p class="list-row-meta">${escapeHtml(lineaDetalle(aviso.mensaje))}</p>
      </div>
      <span class="list-row-aside status status--bad"><span class="status-dot" aria-hidden="true"></span>Activa</span>`;
    lista.prepend(item);
    lista.hidden = false;
    const vacio = document.getElementById('alertas-activas-vacio');
    if (vacio) vacio.hidden = true;
  }

  function notificarNavegador(aviso) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      new Notification(aviso.titulo, { body: lineaDetalle(aviso.mensaje), tag: `fuga-${aviso.id_alerta || Date.now()}` });
    } catch (error) {
      // Algunos navegadores de celular solo dejan notificar desde un service worker.
    }
  }

  // El permiso del navegador se pide con un clic (los navegadores bloquean pedirlo solos).
  const boton = document.getElementById('activar-avisos-navegador');
  if (boton && 'Notification' in window && Notification.permission === 'default') {
    boton.hidden = false;
    boton.addEventListener('click', async () => {
      await Notification.requestPermission();
      boton.hidden = true;
    });
  }

  window.addEventListener('smartdrop:leak-alert', (event) => {
    const aviso = event.detail || {};
    if (aviso.para !== 'residente') return;
    mostrarTarjeta(aviso);
    agregarAAlertasActivas(aviso);
    notificarNavegador(aviso);
  });
}());

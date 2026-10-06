/* Botón "Realizar predicciones": lanza el job, consulta su progreso y muestra una pantalla de carga solo si tarda. */
(function () {
  const SHOW_OVERLAY_AFTER_MS = 1000;
  const POLL_MS = 1000;

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function buildOverlay() {
    const overlay = document.createElement('div');
    overlay.className = 'ml-overlay';
    overlay.setAttribute('role', 'alertdialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = `
      <div class="ml-overlay-card">
        <div class="ml-drops" aria-hidden="true"><span></span><span></span><span></span></div>
        <h2 class="ml-overlay-title">Realizando predicciones…</h2>
        <p class="ml-overlay-step" id="ml-overlay-step">Iniciando</p>
        <div class="ml-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="ml-progress-bar" id="ml-progress-bar"></div>
        </div>
        <p class="ml-overlay-meta"><span id="ml-overlay-percent">0 %</span> · <span id="ml-overlay-elapsed">0 s</span></p>
      </div>`;
    document.body.appendChild(overlay);
    return overlay;
  }

  async function request(url, options) {
    const response = await fetch(url, Object.assign({ credentials: 'same-origin', headers: { Accept: 'application/json' } }, options));
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || body.error || `Error ${response.status}`);
    }
    return response.json();
  }

  window.SmartDropPredictions = {
    /** Devuelve una promesa con el resultado del job; `onProgress(job)` se llama en cada consulta. */
    run({ runUrl, jobUrl, onProgress }) {
      return new Promise(async (resolve, reject) => {
        let overlay = null;
        let overlayTimer = null;
        const startedAt = Date.now();

        const ensureOverlay = () => { if (!overlay) overlay = buildOverlay(); };
        const closeOverlay = () => { if (overlay) { overlay.remove(); overlay = null; } };
        const paint = (job) => {
          if (!overlay) return;
          overlay.querySelector('#ml-overlay-step').textContent = job.step || '…';
          overlay.querySelector('#ml-progress-bar').style.width = `${job.progress}%`;
          overlay.querySelector('.ml-progress').setAttribute('aria-valuenow', String(job.progress));
          overlay.querySelector('#ml-overlay-percent').textContent = `${job.progress} %`;
          overlay.querySelector('#ml-overlay-elapsed').textContent = `${Math.round((Date.now() - startedAt) / 1000)} s`;
        };

        try {
          let job = await request(runUrl, { method: 'POST', headers: { Accept: 'application/json', 'X-CSRFToken': csrfToken() } });
          overlayTimer = window.setTimeout(() => { if (job.status === 'running') { ensureOverlay(); paint(job); } }, SHOW_OVERLAY_AFTER_MS);
          while (job.status === 'running') {
            await new Promise((done) => window.setTimeout(done, POLL_MS));
            job = await request(jobUrl.replace('__ID__', job.job_id));
            if (onProgress) onProgress(job);
            paint(job);
          }
          window.clearTimeout(overlayTimer);
          closeOverlay();
          if (job.status === 'done') resolve(job.result); else reject(new Error(job.error || 'La predicción falló.'));
        } catch (error) {
          window.clearTimeout(overlayTimer);
          closeOverlay();
          reject(error);
        }
      });
    },
  };
}());

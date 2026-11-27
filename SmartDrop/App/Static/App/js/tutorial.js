/* Tutorial para usuarios nuevos (PB045).
 *
 * - Se abre solo la primera vez que el usuario entra a Inicio.
 * - Se puede volver a ver con "Ver tutorial" (menú Cuenta o el manual de usuario).
 * - Solo recuerda en este navegador que ya se vio; no guarda nada en el servidor
 *   ni toca sensores, lecturas o datos.
 */
(function () {
  'use strict';

  var script = document.currentScript;
  if (!script) return;
  var CLAVE = 'smartdrop-tutorial-visto';
  var inicio = script.dataset.inicio || '/';

  // Los mismos 5 pasos que el tutorial de la app.
  var PASOS = [
    { icono: 'ti-building-warehouse', titulo: 'Cómo ver el nivel del tanque',
      texto: 'Bienvenido a SmartDrop. En Inicio ves el nivel de tu tanque, la presión y la calidad del agua. Toca cualquier tarjeta para ver más detalle.' },
    { icono: 'ti-chart-bar', titulo: 'Cómo consultar tu consumo',
      texto: 'En Consumo ves cuántos litros usas por día, semana o mes. En Retroalimentación y Consejos de ahorro te decimos cómo vas y qué puedes mejorar.' },
    { icono: 'ti-bell', titulo: 'Cómo entender las alertas',
      texto: 'La campana de arriba te avisa si hay una fuga, si el agua no está bien o si viene un corte. Revísala cuando tenga un número.' },
    { icono: 'ti-alert-triangle', titulo: 'Cómo reportar problemas',
      texto: 'Si no te llega agua o ves una fuga, entra a Reportar un problema. Puedes adjuntar fotos y hablar con el equipo por el chat del reporte.' },
    { icono: 'ti-book', titulo: 'Dónde encontrar el manual completo',
      texto: 'El Manual de usuario está en el menú Cuenta. Tiene buscador y lo puedes descargar en PDF para leerlo sin internet. Este tutorial lo vuelves a ver en Cuenta → Ver tutorial.' }
  ];

  function visto() {
    try { return localStorage.getItem(CLAVE) === '1'; } catch (e) { return true; }
  }
  function marcarVisto() {
    try { localStorage.setItem(CLAVE, '1'); } catch (e) { /* sin memoria del navegador: no pasa nada */ }
  }

  var dialogo = null;
  var paso = 0;

  function crear() {
    dialogo = document.createElement('dialog');
    dialogo.className = 'tutorial';
    dialogo.setAttribute('aria-labelledby', 'tutorial-titulo');
    dialogo.innerHTML =
      '<button type="button" class="tutorial-cerrar" data-accion="cerrar" aria-label="Cerrar tutorial"><i class="ti ti-x" aria-hidden="true"></i></button>' +
      '<div class="tutorial-icono" aria-hidden="true"><i class="ti"></i></div>' +
      '<p class="tutorial-paso"></p>' +
      '<h2 class="tutorial-titulo" id="tutorial-titulo"></h2>' +
      '<p class="tutorial-texto"></p>' +
      '<div class="tutorial-puntos" aria-hidden="true"></div>' +
      '<div class="tutorial-acciones">' +
      '  <button type="button" class="btn btn--ghost" data-accion="saltar">Saltar</button>' +
      '  <span class="tutorial-espacio"></span>' +
      '  <button type="button" class="btn btn--secondary" data-accion="atras"><i class="ti ti-arrow-left" aria-hidden="true"></i>Atrás</button>' +
      '  <button type="button" class="btn btn--primary" data-accion="siguiente"></button>' +
      '</div>';
    var puntos = dialogo.querySelector('.tutorial-puntos');
    PASOS.forEach(function () { puntos.appendChild(document.createElement('span')); });

    dialogo.addEventListener('click', function (evento) {
      if (evento.target === dialogo) { cerrar(); return; }  // clic fuera de la ventana
      var boton = evento.target.closest('[data-accion]');
      if (!boton) return;
      var accion = boton.dataset.accion;
      if (accion === 'siguiente') {
        if (paso < PASOS.length - 1) { paso++; pintar(); } else { cerrar(); }
      } else if (accion === 'atras') {
        if (paso > 0) { paso--; pintar(); }
      } else {
        cerrar();
      }
    });
    dialogo.addEventListener('close', marcarVisto);
    document.body.appendChild(dialogo);
  }

  function pintar() {
    var datos = PASOS[paso];
    dialogo.querySelector('.tutorial-icono i').className = 'ti ' + datos.icono;
    dialogo.querySelector('.tutorial-paso').textContent = 'Paso ' + (paso + 1) + ' de ' + PASOS.length;
    dialogo.querySelector('.tutorial-titulo').textContent = datos.titulo;
    dialogo.querySelector('.tutorial-texto').textContent = datos.texto;
    dialogo.querySelectorAll('.tutorial-puntos span').forEach(function (punto, i) {
      punto.classList.toggle('is-actual', i === paso);
    });
    dialogo.querySelector('[data-accion="atras"]').hidden = paso === 0;
    var ultimo = paso === PASOS.length - 1;
    dialogo.querySelector('[data-accion="siguiente"]').innerHTML = ultimo
      ? '<i class="ti ti-check" aria-hidden="true"></i>Listo'
      : 'Siguiente<i class="ti ti-arrow-right" aria-hidden="true"></i>';
    dialogo.querySelector('[data-accion="saltar"]').hidden = ultimo;
  }

  function abrir() {
    if (!dialogo) crear();
    if (dialogo.open) return;
    paso = 0;
    pintar();
    if (typeof dialogo.showModal === 'function') dialogo.showModal(); else dialogo.setAttribute('open', '');
    dialogo.querySelector('[data-accion="siguiente"]').focus();
  }

  function cerrar() {
    marcarVisto();
    if (dialogo.open && typeof dialogo.close === 'function') dialogo.close(); else dialogo.removeAttribute('open');
  }

  // Botones y enlaces "Ver tutorial": si ya estamos en Inicio se abre aquí mismo.
  document.addEventListener('click', function (evento) {
    var enlace = evento.target.closest('[data-abrir-tutorial]');
    if (!enlace || window.location.pathname !== inicio) return;
    evento.preventDefault();
    abrir();
  });

  var pidioVerlo = new URLSearchParams(window.location.search).get('tutorial') === '1';
  if (window.location.pathname === inicio && (pidioVerlo || !visto())) {
    if (pidioVerlo && window.history.replaceState) {
      window.history.replaceState(null, '', inicio);  // que recargar la página no lo vuelva a abrir
    }
    window.setTimeout(abrir, 400);
  }
})();

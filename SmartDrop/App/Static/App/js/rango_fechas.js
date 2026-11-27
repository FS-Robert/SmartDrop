// Revisa los formularios que tienen "Desde" y "Hasta" (PB038).
// Si la fecha de inicio queda después de la fecha de fin, no deja enviar el formulario
// y muestra un aviso debajo de los campos. El servidor también lo revisa por si acaso.
(function () {
  var MENSAJE = 'La fecha de inicio no puede ser posterior a la fecha de fin.';

  document.querySelectorAll('form').forEach(function (form) {
    var desde = form.querySelector('input[type="date"][name="desde"]');
    var hasta = form.querySelector('input[type="date"][name="hasta"]');
    if (!desde || !hasta) return;

    var aviso = document.createElement('p');
    aviso.className = 'rango-fechas-error';
    aviso.setAttribute('role', 'alert');
    aviso.hidden = true;
    aviso.innerHTML = '<i class="ti ti-alert-triangle" aria-hidden="true"></i> ' + MENSAJE;
    form.appendChild(aviso);

    function revisar() {
      var malo = desde.value && hasta.value && desde.value > hasta.value;
      aviso.hidden = !malo;
      desde.setAttribute('aria-invalid', malo ? 'true' : 'false');
      hasta.setAttribute('aria-invalid', malo ? 'true' : 'false');
      return !malo;
    }

    desde.addEventListener('change', revisar);
    hasta.addEventListener('change', revisar);
    form.addEventListener('submit', function (evento) {
      if (!revisar()) {
        evento.preventDefault();
        desde.focus();
      }
    });
  });
}());

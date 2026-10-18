/* Páginas de acceso: mostrar/ocultar contraseña y estado de carga del botón al enviar. */
(function () {
  'use strict';

  document.querySelectorAll('.toggle-password').forEach(function (button) {
    var input = document.getElementById(button.getAttribute('aria-controls'));
    if (!input) return;
    button.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-pressed', show ? 'true' : 'false');
      button.setAttribute('aria-label', show ? 'Ocultar contraseña' : 'Mostrar contraseña');
      input.focus();
    });
  });

  document.querySelectorAll('form.auth-form').forEach(function (form) {
    var submit = form.querySelector('.auth-submit');
    form.addEventListener('submit', function () {
      if (!submit) return;
      submit.disabled = true;
      submit.classList.add('is-loading');
    });
    // Al volver con el botón "atrás" el navegador puede restaurar la página con el botón deshabilitado.
    window.addEventListener('pageshow', function () {
      if (!submit) return;
      submit.disabled = false;
      submit.classList.remove('is-loading');
    });
  });
})();

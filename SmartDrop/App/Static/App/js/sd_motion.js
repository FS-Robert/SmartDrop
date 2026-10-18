/* Movimiento de SmartDrop: aparición escalonada, números que cuentan y onda al pulsar botones.
 *
 * Todo es opcional: si el usuario pidió "reducir movimiento" este archivo no hace nada, y sin
 * JavaScript la página se ve completa (el CSS solo oculta lo que este script marca con .reveal).
 * Estilos en smartdrop.css (sección 6c).
 *
 *   <p data-countup>1 250 L</p>   -> el primer número del texto sube de 0 al valor cuando se ve.
 */
(function () {
  'use strict';

  var query = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
  if (query && query.matches) return;

  var root = document.documentElement;
  var hasObserver = 'IntersectionObserver' in window;

  // ── Aparición escalonada ──────────────────────────────────────────────
  var REVEAL = '.page-head, .home-hero, .panel, .tile, .tip-card, .card-more, .reporte-card, .comunidad-card';

  function revealTargets() {
    var all = Array.prototype.slice.call(document.querySelectorAll('.content ' + REVEAL.split(', ').join(', .content ')));
    return all.filter(function (el) {
      // Si un ancestro también aparece, ese ya mueve a sus hijos: evitamos animar dos veces.
      var parent = el.parentElement;
      return !(parent && parent.closest(REVEAL));
    });
  }

  function setupReveal() {
    var targets = revealTargets();
    if (!targets.length || !hasObserver) return;

    root.classList.add('motion');
    targets.forEach(function (el) { el.classList.add('reveal'); });

    var observer = new IntersectionObserver(function (entries) {
      var batch = 0;
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        el.style.setProperty('--i', String(Math.min(batch, 6)));
        el.classList.add('is-in');
        observer.unobserve(el);
        batch += 1;
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0 });

    targets.forEach(function (el) { observer.observe(el); });

    // Red de seguridad: nada se queda oculto si el observador no llegó a avisar.
    window.setTimeout(function () {
      targets.forEach(function (el) {
        var rect = el.getBoundingClientRect();
        if (rect.top < window.innerHeight && rect.bottom > 0) el.classList.add('is-in');
      });
    }, 2500);
  }

  // ── Números que cuentan ───────────────────────────────────────────────
  var NUMBER = /^(\s*)(-?\d[\d.,]*)([\s\S]*)$/;

  function parseNumber(raw) {
    var lastDot = raw.lastIndexOf('.');
    var lastComma = raw.lastIndexOf(',');
    var sep = Math.max(lastDot, lastComma);
    var decimals = 0;
    var value;
    if (sep >= 0) {
      var tail = raw.length - sep - 1;
      var other = lastDot > lastComma ? ',' : '.';
      var mixed = raw.indexOf(other) >= 0;
      var single = raw.split(raw.charAt(sep)).length === 2;
      if (mixed || (single && tail !== 3)) {
        decimals = tail;
        value = parseFloat(raw.slice(0, sep).replace(/[.,]/g, '') + '.' + raw.slice(sep + 1));
        return { value: value, decimals: decimals, decimal: raw.charAt(sep) };
      }
    }
    value = parseFloat(raw.replace(/[.,]/g, ''));
    return { value: value, decimals: 0, decimal: '.' };
  }

  function firstTextNode(el) {
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      if (NUMBER.test(node.nodeValue)) return node;
      if (node.nodeValue.trim()) return null;
    }
    return null;
  }

  function countUp(el) {
    var node = firstTextNode(el);
    if (!node) return;
    var original = node.nodeValue;
    var match = NUMBER.exec(original);
    var parsed = parseNumber(match[2]);
    if (!isFinite(parsed.value) || parsed.value === 0) return;

    var big = Math.abs(parsed.value) >= 1000;
    var duration = 800;
    var started = null;
    var last = null;

    function format(n) {
      var text = n.toFixed(parsed.decimals);
      if (parsed.decimals && parsed.decimal === ',') text = text.replace('.', ',');
      if (big && parsed.decimals === 0) text = String(Math.round(n));
      return text;
    }

    function frame(now) {
      if (started === null) started = now;
      var t = Math.min((now - started) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      if (node.nodeValue !== last && last !== null) return;  // otro script cambió el valor: no lo pisamos
      if (t < 1) {
        last = match[1] + format(parsed.value * eased) + match[3];
        node.nodeValue = last;
        window.requestAnimationFrame(frame);
      } else {
        node.nodeValue = original;
      }
    }

    node.nodeValue = match[1] + format(0) + match[3];
    last = node.nodeValue;
    window.requestAnimationFrame(frame);
  }

  function setupCountUp() {
    var items = Array.prototype.slice.call(document.querySelectorAll('[data-countup]'));
    if (!items.length) return;
    if (!hasObserver) return;
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        observer.unobserve(entry.target);
        countUp(entry.target);
      });
    }, { threshold: 0.3 });
    items.forEach(function (el) { observer.observe(el); });
  }

  // ── Onda al pulsar un botón ───────────────────────────────────────────
  function setupRipple() {
    document.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      var button = event.target.closest && event.target.closest('.btn');
      if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return;
      var rect = button.getBoundingClientRect();
      var wave = document.createElement('span');
      wave.className = 'btn-ripple';
      wave.setAttribute('aria-hidden', 'true');
      wave.style.left = (event.clientX - rect.left) + 'px';
      wave.style.top = (event.clientY - rect.top) + 'px';
      button.classList.add('btn--ripple');
      button.appendChild(wave);
      wave.addEventListener('animationend', function () { wave.remove(); });
    }, { passive: true });
  }

  function start() {
    try {
      setupReveal();
      setupCountUp();
      setupRipple();
    } catch (error) {
      // El movimiento es un adorno: si algo falla, la página queda tal cual.
      root.classList.remove('motion');
      Array.prototype.forEach.call(document.querySelectorAll('.reveal'), function (el) {
        el.classList.add('is-in');
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();

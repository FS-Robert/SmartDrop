/* Traducción de la interfaz al inglés con el catálogo compartido con la app (window.SD_I18N).
 *
 * Orden: frase exacta → plantilla con huecos ("Hay {0} alertas") → por segmentos ("Última lectura: 10:30").
 * Se traduce la página al cargar y todo lo que el JavaScript agrega después (MutationObserver), además de
 * las etiquetas de Chart.js y los diálogos alert/confirm. Lo escrito por el usuario no se toca.
 */
(function () {
  'use strict';
  const catalog = window.SD_I18N;
  const root = document.documentElement;
  if (!catalog) { root.classList.remove('sd-i18n-pending'); return; }

  const phrases = new Map(Object.entries(catalog.frases || {}));
  const upper = new Map();
  phrases.forEach((en, es) => upper.set(es.toUpperCase(), en.toUpperCase()));
  const HOLE = /\{(\d+)\}/g;
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const templates = Object.entries(catalog.plantillas || {})
    .sort((a, b) => b[0].replace(HOLE, '').length - a[0].replace(HOLE, '').length)
    .map(([es, en]) => {
      let regex = '^';
      let from = 0;
      es.replace(HOLE, (match, n, offset) => {
        regex += escapeRe(es.slice(from, offset)) + '([\\s\\S]+?)';
        from = offset + match.length;
        return match;
      });
      regex += escapeRe(es.slice(from)) + '$';
      return { re: new RegExp(regex), en };
    });
  const SEPARATOR = /(\s*[:·•●|\n]\s*|\s+[-–—]\s+|\s*\(\s*|\s*\)\s*|\s*,\s+|\s+→\s+)/;
  const FINAL = /^([\s\S]*?)([.…!?:]+)$/;
  const NO_LETTERS = /^[^A-Za-zÀ-ÿ]*$/;
  const cache = new Map();

  function exact(text) {
    if (phrases.has(text)) return phrases.get(text);
    if (text === text.toUpperCase() && text !== text.toLowerCase() && upper.has(text)) return upper.get(text);
    return null;
  }

  function core(text) {
    const direct = exact(text);
    if (direct !== null) return direct;
    // Textos de plantilla repartidos en varias líneas: se comparan con los espacios normalizados.
    const collapsed = text.replace(/\s+/g, ' ');
    if (collapsed !== text) return core(collapsed);
    const punct = FINAL.exec(text);
    if (punct && punct[1]) {
      const bare = exact(punct[1]);
      if (bare !== null) return bare + punct[2];
    }
    for (const { re, en } of templates) {
      const m = re.exec(text);
      if (!m) continue;
      // Cada hueco puede ser a su vez un texto del catálogo ("Baja", otra frase…): se traduce completo.
      return en.replace(HOLE, (_, n) => withSpaces(m[Number(n) + 1] || '', true));
    }
    return null;
  }

  function withSpaces(text, bySegments) {
    const match = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    if (!match[2]) return text;
    let translated = core(match[2]);
    if (translated === null && bySegments) translated = segments(match[2]);
    return translated === null ? text : match[1] + translated + match[3];
  }

  function segments(text) {
    const parts = text.split(SEPARATOR);
    if (parts.length < 2) return null;
    let changed = false;
    const out = parts.map((part, i) => {
      if (i % 2 === 1 || !part) return part;
      const translated = withSpaces(part, false);
      if (translated !== part) changed = true;
      return translated;
    });
    return changed ? out.join('') : null;
  }

  function t(text) {
    if (typeof text !== 'string' || !text || NO_LETTERS.test(text)) return text;
    if (cache.has(text)) return cache.get(text);
    const result = withSpaces(text, true);
    if (cache.size > 2000) cache.clear();
    cache.set(text, result);
    return result;
  }

  // ── DOM ───────────────────────────────────────────────────────────────────
  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'NOSCRIPT']);
  const ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt'];

  function skipped(element) {
    return !element || SKIP.has(element.tagName) || element.isContentEditable ||
      (element.closest && element.closest('[data-no-translate]'));
  }

  function translateText(node) {
    if (skipped(node.parentElement)) return;
    const translated = t(node.nodeValue);
    if (translated !== node.nodeValue) node.nodeValue = translated;
  }

  function translateAttributes(element) {
    if (skipped(element)) return;
    for (const name of ATTRIBUTES) {
      const value = element.getAttribute && element.getAttribute(name);
      if (value) {
        const translated = t(value);
        if (translated !== value) element.setAttribute(name, translated);
      }
    }
    if (element.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(element.type) && element.value) {
      const translated = t(element.value);
      if (translated !== element.value) element.value = translated;
    }
    if (element.tagName === 'OPTION' && element.textContent) {
      // El texto de <option> ya es un nodo de texto; nada extra.
    }
  }

  function translateTree(node) {
    if (node.nodeType === Node.TEXT_NODE) { translateText(node); return; }
    if (node.nodeType !== Node.ELEMENT_NODE || skipped(node)) return;
    translateAttributes(node);
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === Node.ELEMENT_NODE && skipped(n)) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let current = walker.nextNode();
    while (current) {
      if (current.nodeType === Node.TEXT_NODE) translateText(current); else translateAttributes(current);
      current = walker.nextNode();
    }
  }

  function start() {
    document.title = t(document.title);
    translateTree(document.body);
    root.classList.remove('sd-i18n-pending');
    new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === 'characterData') translateText(mutation.target);
        else if (mutation.type === 'attributes') translateAttributes(mutation.target);
        else mutation.addedNodes.forEach(translateTree);
      }
    }).observe(document.body, {
      childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES,
    });
  }

  // ── Chart.js: etiquetas de series y ejes (se dibujan en un canvas, no en el DOM) ──────
  function translateChart(chart) {
    const data = chart.data || {};
    if (Array.isArray(data.labels)) data.labels = data.labels.map((label) => (typeof label === 'string' ? t(label) : label));
    (data.datasets || []).forEach((dataset) => { if (typeof dataset.label === 'string') dataset.label = t(dataset.label); });
    const scales = (chart.options && chart.options.scales) || {};
    Object.values(scales).forEach((scale) => {
      if (scale && scale.title && typeof scale.title.text === 'string') scale.title.text = t(scale.title.text);
    });
  }
  const chartPlugin = { id: 'sdI18n', beforeInit: translateChart, beforeUpdate: translateChart };
  let ChartRef = window.Chart;
  if (ChartRef && ChartRef.register) ChartRef.register(chartPlugin);
  else {
    Object.defineProperty(window, 'Chart', {
      configurable: true,
      get() { return ChartRef; },
      set(value) {
        ChartRef = value;
        if (value && value.register) value.register(chartPlugin);
      },
    });
  }

  // ── Diálogos del navegador ─────────────────────────────────────────────────
  ['alert', 'confirm', 'prompt'].forEach((name) => {
    const original = window[name];
    if (typeof original === 'function') window[name] = (message, ...rest) => original.call(window, t(String(message)), ...rest);
  });

  window.SmartDropI18n = { t };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
  // Por seguridad, la página nunca queda oculta aunque algo falle.
  window.setTimeout(() => root.classList.remove('sd-i18n-pending'), 1500);
}());

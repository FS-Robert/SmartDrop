/* Tema de Chart.js con los tokens de smartdrop.css ("Agua de la zona").
 *
 * Cargar después de chart.umd.min.js. Ajusta Chart.defaults (tipografía, cuadrícula, ejes, tooltip,
 * leyenda) y expone window.SDCharts con:
 *   SDCharts.color('agua')          -> valor del token --c-agua (sirve en modo claro y oscuro)
 *   SDCharts.alpha('agua', 0.15)    -> el mismo color con transparencia
 *   SDCharts.series(i)              -> color de la serie i (agua, añil, ok, sol)
 * Si el tema cambia (data-theme / clase dark-mode), las gráficas existentes se repintan.
 */
(function () {
  'use strict';
  if (!window.Chart) return;

  const root = document.documentElement;
  const SERIES = ['agua', 'anil', 'ok-dot', 'warn-dot'];

  function color(name) {
    const target = document.body || root;
    const value = getComputedStyle(target).getPropertyValue(`--c-${name}`).trim();
    return value || '#1D8FB8';
  }

  function alpha(name, amount) {
    const value = color(name);
    const hex = value.replace('#', '');
    if (/^[0-9a-f]{6}$/i.test(hex)) {
      const n = parseInt(hex, 16);
      return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${amount})`;
    }
    return value;
  }

  function font(name) {
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    return value || 'system-ui, sans-serif';
  }

  function applyDefaults() {
    const d = window.Chart.defaults;
    d.font.family = font('--font-text');
    d.font.size = 13;
    d.color = color('ink-3');
    d.borderColor = color('line');
    d.maintainAspectRatio = false;
    // Las gráficas se dibujan con calma al aparecer (nada de movimiento si el usuario lo pidió).
    const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    d.animation = calm ? false : { duration: 900, easing: 'easeOutQuart' };
    d.elements.line.borderWidth = 2.5;
    d.elements.line.tension = 0.35;
    d.elements.point.radius = 0;
    d.elements.point.hoverRadius = 5;
    d.elements.bar.borderRadius = 6;
    d.elements.bar.borderSkipped = false;
    d.plugins.legend.labels.usePointStyle = true;
    d.plugins.legend.labels.pointStyle = 'rectRounded';
    d.plugins.legend.labels.boxWidth = 10;
    d.plugins.legend.labels.boxHeight = 10;
    d.plugins.legend.labels.color = color('ink-2');
    d.plugins.tooltip.backgroundColor = color('ink');
    d.plugins.tooltip.titleColor = color('surface');
    d.plugins.tooltip.bodyColor = color('surface');
    d.plugins.tooltip.padding = 10;
    d.plugins.tooltip.cornerRadius = 10;
    d.plugins.tooltip.displayColors = true;
    d.plugins.tooltip.boxPadding = 4;
    d.scale.grid.color = color('line');
    d.scale.border.display = false;
    d.scale.ticks.color = color('ink-3');
  }

  applyDefaults();

  window.SDCharts = {
    color,
    alpha,
    series(i) { return color(SERIES[i % SERIES.length]); },
    refresh() {
      applyDefaults();
      Object.values(window.Chart.instances || {}).forEach((chart) => {
        if (typeof chart.$sdRecolor === 'function') chart.$sdRecolor();
        chart.update('none');
      });
    },
  };

  // Repintar si el usuario cambia entre modo claro y oscuro con la página abierta.
  const observer = new MutationObserver(() => window.SDCharts.refresh());
  const watch = { attributes: true, attributeFilter: ['data-theme', 'class'] };
  observer.observe(root, watch);
  if (document.body) observer.observe(document.body, watch);
}());

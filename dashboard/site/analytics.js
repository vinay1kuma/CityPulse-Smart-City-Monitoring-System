'use strict';
/* Analytics page: historical series from PostgreSQL, zone comparison table, city heatmap, CSV export. */
(function () {
  let timer = null, data = null, summary = [];

  function qs() { return { metric: $('a-metric').value, minutes: $('a-range').value }; }

  function init() {
    const m = $('a-metric'), b = $('a-band'); clear(m); clear(b);
    for (const k in App.cfg.metrics) m.appendChild(option(k, App.cfg.metrics[k].label));
    b.appendChild(option('', 'None'));
    for (const z in App.cfg.zones) b.appendChild(option(z, App.cfg.zones[z]));
    b.value = Object.keys(App.cfg.zones)[0];
    m.onchange = load; $('a-range').onchange = load; b.onchange = () => draw();
    const lg = $('a-legend'); clear(lg);
    for (const z in App.cfg.zones) { const s = el('span'), i = el('i'); i.style.background = ZONE_COLORS[z]; s.appendChild(i); s.appendChild(document.createTextNode(App.cfg.zones[z])); lg.appendChild(s); }
    [['#fbbf24', 'warning limit'], ['#f87171', 'critical limit']].forEach(([c, t]) => { const s = el('span'), i = el('i'); i.style.background = c; s.appendChild(i); s.appendChild(document.createTextNode(t)); lg.appendChild(s); });
    updateLinks();
  }

  function updateLinks() {
    const { metric, minutes } = qs();
    $('a-csv').href = '/api/analytics/export.csv?metric=' + encodeURIComponent(metric) + '&minutes=' + minutes;
    $('a-csv-all').href = '/api/analytics/export.csv?minutes=' + minutes;
  }

  async function load() {
    updateLinks();
    const { metric, minutes } = qs();
    try {
      const [series, sum] = await Promise.all([
        getJSON('/api/analytics/series?metric=' + encodeURIComponent(metric) + '&minutes=' + minutes),
        getJSON('/api/analytics/summary?minutes=' + minutes),
      ]);
      // ignore a response that arrived after the user changed the selection
      if (qs().metric !== metric || qs().minutes !== minutes) return;
      data = series; summary = sum;
      draw(); renderTable(); renderHeat();
      const total = Object.values(series.series).reduce((n, a) => n + a.length, 0);
      $('a-note').textContent = total < 10
        ? 'Only a short history exists yet. Data accumulates while the system runs, so longer ranges fill up over time.'
        : 'Each point averages ' + series.bucket + ' s of fog-aggregated readings. History is read from PostgreSQL.';
    } catch (e) { $('a-note').textContent = 'Could not load analytics: ' + e.message; }
  }

  function draw() {
    if (App.current !== 'analytics' || !data) return;
    const metric = $('a-metric').value, cfg = App.cfg.metrics[metric], band = $('a-band').value;
    const series = [], bands = [];
    let lo = Infinity, hi = -Infinity, tmin = Infinity, tmax = -Infinity;
    for (const z in data.series) {
      const pts = data.series[z].map(p => ({ t: p.t * 1000, v: p.avg }));
      series.push({ color: ZONE_COLORS[z], pts, width: 2 });
      data.series[z].forEach(p => {
        const l = z === band ? p.min : p.avg, h = z === band ? p.max : p.avg;
        lo = Math.min(lo, l); hi = Math.max(hi, h); tmin = Math.min(tmin, p.t * 1000); tmax = Math.max(tmax, p.t * 1000);
      });
      if (z === band) bands.push({ color: ZONE_COLORS[z], pts: data.series[z].map(p => ({ t: p.t * 1000, lo: p.min, hi: p.max })) });
    }
    if (!isFinite(lo)) { lo = cfg.ymin; hi = cfg.ymax; }
    const pad = Math.max((hi - lo) * 0.15, 1);
    const now = Date.now();
    Chart.draw($('achart'), {
      t0: Math.min(tmin, now - data.minutes * 60000), t1: now, y0: lo - pad, y1: hi + pad, series, bands,
      hlines: [{ v: cfg.warn, color: '#fbbf24' }, { v: cfg.crit, color: '#f87171' }],
      xfmt: t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    });
  }

  function pct(v) { return v < 0.05 ? '0%' : v.toFixed(1) + '%'; }

  function renderTable() {
    const tb = $('a-table'); clear(tb);
    const head = el('tr');
    ['Zone', 'Average', 'Min', 'Max', 'In warning', 'In critical', 'Samples'].forEach(h => head.appendChild(el('th', '', h)));
    tb.appendChild(head);
    const metric = $('a-metric').value, cfg = App.cfg.metrics[metric];
    for (const z in App.cfg.zones) {
      const r = summary.find(x => x.zone === z && x.metric === metric); const tr = el('tr');
      tr.appendChild(el('td', '', App.cfg.zones[z]));
      if (!r) { for (let i = 0; i < 6; i++) tr.appendChild(el('td', '', '--')); }
      else {
        [fmtNum(r.avg) + ' ' + cfg.unit, fmtNum(r.min), fmtNum(r.max), pct(r.pct_warning), pct(r.pct_critical), r.samples]
          .forEach(v => tr.appendChild(el('td', '', String(v))));
      }
      tb.appendChild(tr);
    }
  }

  function renderHeat() {
    const tb = $('a-heat'); clear(tb);
    const head = el('tr'); head.appendChild(el('th', '', 'Zone'));
    for (const m in App.cfg.metrics) head.appendChild(el('th', '', App.cfg.metrics[m].label));
    tb.appendChild(head);
    for (const z in App.cfg.zones) {
      const tr = el('tr'); tr.appendChild(el('td', '', App.cfg.zones[z]));
      for (const m in App.cfg.metrics) {
        const r = summary.find(x => x.zone === z && x.metric === m), td = el('td');
        if (r) {
          td.textContent = fmtNum(r.avg) + ' ' + App.cfg.metrics[m].unit;
          if (r.pct_critical > 0.5) td.className = 'crit'; else if (r.pct_warning > 0.5) td.className = 'hot';
        } else td.textContent = '--';
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
  }

  App.pages.analytics = {
    init,
    show() { load(); clearInterval(timer); timer = setInterval(() => { if (App.current === 'analytics') load(); else clearInterval(timer); }, 10000); },
  };
  App.on('tick', () => { if (App.current === 'analytics') draw(); });
})();

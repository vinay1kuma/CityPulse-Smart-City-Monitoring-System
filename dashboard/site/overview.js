'use strict';
/* Overview page: zone cards, live trend + forecast, alert feed, incident injection, forecast table, KPIs. */
(function () {
  const tiles = {};
  let activeMetric = 'temperature';
  const HORIZON_MS = 30000;

  function buildZones() {
    const wrap = $('zones'); clear(wrap);
    for (const z in App.cfg.zones) {
      const card = el('div', 'card'), head = el('div', 'zonehead'), left = el('div');
      left.appendChild(el('div', 'zonename', App.cfg.zones[z]));
      left.appendChild(el('div', 'zoneid', 'Zone ' + z + ' / fog node ' + z));
      const pill = el('span', 'pill normal', 'ALL NORMAL');
      head.appendChild(left); head.appendChild(pill); card.appendChild(head);
      const grid = el('div', 'tiles');
      tiles[z] = { pill, items: {} };
      for (const m in App.cfg.metrics) {
        const t = el('div', 'tile normal');
        t.appendChild(el('div', 'tlabel', App.cfg.metrics[m].label));
        const v = el('div', 'tvalue', '--'); v.appendChild(el('small', '', App.cfg.metrics[m].unit));
        const spark = el('canvas', 'spark');
        t.appendChild(v); t.appendChild(spark); grid.appendChild(t);
        tiles[z].items[m] = { tile: t, value: v, spark };
      }
      card.appendChild(grid); wrap.appendChild(card);
    }
  }

  function buildTabs() {
    const tabs = $('tabs'); clear(tabs);
    for (const m in App.cfg.metrics) {
      const b = el('button', 'tab' + (m === activeMetric ? ' active' : ''), App.cfg.metrics[m].label);
      b.onclick = () => { activeMetric = m; buildTabs(); drawChart(); };
      tabs.appendChild(b);
    }
    const lg = $('legend'); clear(lg);
    const add = (color, text) => { const s = el('span'), i = el('i'); i.style.background = color; s.appendChild(i); s.appendChild(document.createTextNode(text)); lg.appendChild(s); };
    for (const z in App.cfg.zones) add(ZONE_COLORS[z], App.cfg.zones[z]);
    add('#fbbf24', 'warning limit'); add('#f87171', 'critical limit'); add('#a78bfa', 'dashed = forecast');
  }

  function buildFaultForm() {
    const fz = $('fzone'), fm = $('fmetric'); clear(fz); clear(fm);
    for (const z in App.cfg.zones) fz.appendChild(option(z, App.cfg.zones[z]));
    for (const m in App.cfg.metrics) fm.appendChild(option(m, App.cfg.metrics[m].label));
    $('btnSudden').onclick = () => inject('sudden', 20);
    $('btnGradual').onclick = () => inject('gradual', 70);
  }
  async function inject(mode, duration) {
    const zone = $('fzone').value, metric = $('fmetric').value;
    try {
      await sendJSON('/api/fault', 'POST', { zone, metric, mode, duration });
      const what = mode === 'gradual' ? 'Developing incident' : 'Sudden emergency';
      App.banner(what + ' started in ' + App.cfg.zones[zone] + ' (' + App.cfg.metrics[metric].label + '). Watch the zone card, forecast and alert feed...', 'info', 5000);
    } catch (e) { App.banner('Could not start incident: ' + e.message, 'warning', 5000); }
  }

  /* ----- zone tiles ----- */
  function updateTile(m) {
    const t = tiles[m.zone] && tiles[m.zone].items[m.metric]; if (!t) return;
    t.value.firstChild.nodeValue = fmtNum(m.avg);
    t.tile.className = 'tile ' + m.status;
    const cfg = App.cfg.metrics[m.metric];
    Chart.spark(t.spark, App.series[m.metric][m.zone], cfg.ymin, cfg.ymax, STATUS_COLOR[m.status]);
    const worst = worstStatus(m.zone), p = tiles[m.zone].pill;
    p.className = 'pill ' + worst;
    p.textContent = worst === 'normal' ? 'ALL NORMAL' : worst.toUpperCase();
  }

  /* ----- main chart ----- */
  function drawChart() {
    if (App.current !== 'overview') return;
    const cfg = App.cfg.metrics[activeMetric], now = Date.now();
    const series = [];
    for (const z in App.cfg.zones) {
      series.push({ color: ZONE_COLORS[z], pts: App.series[activeMetric][z], width: 2 });
      const f = App.forecast[z][activeMetric], pts = App.series[activeMetric][z];
      if (f && f.r2 >= 0.5 && pts.length) {
        const last = pts[pts.length - 1];
        series.push({ color: ZONE_COLORS[z], dash: [5, 4], width: 2,
                      pts: [{ t: last.t, v: f.current }, { t: last.t + HORIZON_MS, v: f.predicted }] });
      }
    }
    Chart.draw($('chart'), {
      t0: now - 150000, t1: now + 40000, y0: cfg.ymin, y1: cfg.ymax, series,
      hlines: [{ v: cfg.warn, color: '#fbbf24' }, { v: cfg.crit, color: '#f87171' }],
      vlines: [{ t: now, color: '#8b97b5' }],
    });
  }

  /* ----- alert feed ----- */
  function renderAlerts() {
    const ul = $('alerts'); clear(ul);
    const list = App.alerts.slice(0, 40);
    if (!list.length) { ul.appendChild(el('li', 'empty', 'No alerts yet. City is calm.')); return; }
    list.forEach(a => {
      const li = el('li'); li.appendChild(el('div', 'bar ' + a.level));
      const body = el('div'); body.appendChild(el('div', '', a.message));
      const t = el('time', '', fmtTime(a.ts)); if (a.level === 'predicted') t.appendChild(el('span', 'tag', 'predicted'));
      body.appendChild(t); li.appendChild(body); ul.appendChild(li);
    });
  }

  /* ----- forecast table ----- */
  function renderForecast() {
    const tb = $('fctable'); clear(tb);
    const head = el('tr'); head.appendChild(el('th', '', 'Sensor'));
    for (const z in App.cfg.zones) head.appendChild(el('th', '', App.cfg.zones[z]));
    tb.appendChild(head);
    for (const m in App.cfg.metrics) {
      const cfg = App.cfg.metrics[m], tr = el('tr'); tr.appendChild(el('td', '', cfg.label));
      for (const z in App.cfg.zones) {
        const f = App.forecast[z][m], td = el('td');
        if (!f) { td.textContent = '...'; }
        else {
          const range = cfg.ymax - cfg.ymin, delta = f.predicted - f.current;
          const arrow = f.r2 < 0.5 || Math.abs(delta) < 0.02 * range ? '\u2192' : (delta > 0 ? '\u2197' : '\u2198');
          td.textContent = arrow + ' ' + fmtNum(f.predicted, 0) + ' ' + cfg.unit;
          if (f.will_breach) td.className = 'risk';
          else if (f.predicted >= cfg.crit) td.className = 'crit';
          else if (f.predicted >= cfg.warn) td.className = 'hot';
        }
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
  }

  /* ----- KPIs ----- */
  function renderStats(s) {
    $('kraw').textContent = s.rawRate === null ? '--' : Math.round(s.rawRate);
    $('kagg').textContent = s.aggRate === null ? '--' : s.aggRate.toFixed(1);
    $('kred').textContent = s.reduction_pct + '%';
    $('kalerts').textContent = s.alert_count;
    $('knotif').textContent = s.notification_count;
    $('alertcount').textContent = s.alert_count + ' total';
    const fs = $('fogstatus'); clear(fs);
    for (const z in s.fog) {
      const row = el('div', 'row'); row.appendChild(el('span', '', 'Fog node ' + z + ' (' + App.cfg.zones[z] + ')'));
      row.appendChild(el('b', '', s.fog[z] ? 'online' : 'offline')); fs.appendChild(row);
    }
    $('procs').textContent = s.processors.length + ' running';
  }

  App.pages.overview = {
    init() { buildZones(); buildTabs(); buildFaultForm(); renderAlerts(); renderForecast(); },
    show() { renderAlerts(); renderForecast(); drawChart(); for (const z in App.latest) for (const m in App.latest[z]) updateTile(App.latest[z][m]); },
  };
  App.on('telemetry', m => { if (tiles[m.zone]) updateTile(m); });
  App.on('alert', () => renderAlerts());
  App.on('forecast', () => renderForecast());
  App.on('stats', renderStats);
  App.on('tick', drawChart);
})();

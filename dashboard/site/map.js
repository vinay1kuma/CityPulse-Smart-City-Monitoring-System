'use strict';
/* City map page: schematic SVG map with live zone status, sensor activity and a detail panel. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const W = 900, H = 560;
  const nodes = {};
  let selected = null;

  function svg(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function project(g) {   // lat/lon -> SVG coordinates (schematic projection)
    return { x: 60 + ((g.lon - 78.62) / 0.24) * 780, y: 50 + ((10.90 - g.lat) / 0.18) * 460 };
  }

  function build() {
    const root = $('mapsvg'); clear(root);
    const defs = svg('defs', {}, root);
    const pat = svg('pattern', { id: 'grid', width: 40, height: 40, patternUnits: 'userSpaceOnUse' }, defs);
    svg('path', { d: 'M40 0H0V40', fill: 'none', stroke: '#16213f', 'stroke-width': 1 }, pat);
    svg('rect', { width: W, height: H, fill: 'url(#grid)' }, root);
    // river (decorative)
    svg('path', { d: 'M-20 160 C 160 230 330 150 520 230 S 800 330 920 300', fill: 'none', stroke: '#16406b', 'stroke-width': 26, 'stroke-linecap': 'round', opacity: 0.85 }, root);
    const rl = svg('text', { x: 560, y: 262, fill: '#3b7bb5', 'font-size': 13, 'font-style': 'italic' }, root); rl.textContent = 'Kaveri river (schematic)';

    const pos = {};
    for (const z in App.cfg.zones) pos[z] = project(App.cfg.geo[z]);
    const ids = Object.keys(pos);
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      const a = pos[ids[i]], b = pos[ids[j]], mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 + 30;
      svg('path', { d: 'M' + a.x + ' ' + a.y + ' Q ' + mx + ' ' + my + ' ' + b.x + ' ' + b.y, fill: 'none', stroke: '#233763', 'stroke-width': 3, 'stroke-dasharray': '2 8', 'stroke-linecap': 'round' }, root);
    }
    for (const z in App.cfg.zones) {
      const p = pos[z];
      const g = svg('g', { class: 'zonegroup', transform: 'translate(' + p.x + ',' + p.y + ')' }, root);
      const halo = svg('circle', { r: 52, fill: STATUS_COLOR.normal, opacity: 0.18, class: 'halo' }, g);
      const ring = svg('circle', { r: 34, fill: '#0f1a33', stroke: STATUS_COLOR.normal, 'stroke-width': 4, class: 'ring' }, g);
      const letter = svg('text', { 'text-anchor': 'middle', y: 8, fill: '#e6ebf7', 'font-size': 22, 'font-weight': 700 }, g); letter.textContent = z;
      const name = svg('text', { 'text-anchor': 'middle', y: 78, fill: '#e6ebf7', 'font-size': 15, 'font-weight': 600 }, g); name.textContent = App.cfg.zones[z];
      const info = svg('text', { 'text-anchor': 'middle', y: 96, fill: '#8b97b5', 'font-size': 12 }, g);
      // sensor dots on an outer orbit (3 per sensor type)
      const dots = {};
      let k = 0; const total = Object.keys(App.cfg.metrics).length * 3;
      for (const m in App.cfg.metrics) {
        dots[m] = [];
        for (let s = 0; s < 3; s++, k++) {
          const ang = (k / total) * Math.PI * 2 - Math.PI / 2;
          dots[m].push(svg('circle', { cx: Math.cos(ang) * 46, cy: Math.sin(ang) * 46, r: 3.5, fill: '#22d3ee', opacity: 0.35, class: 'sdot' }, g));
        }
      }
      g.addEventListener('click', () => select(z));
      nodes[z] = { g, halo, ring, info, dots };
    }
    select(Object.keys(App.cfg.zones)[0]);
  }

  function select(z) {
    selected = z;
    for (const k in nodes) nodes[k].g.classList.toggle('sel', k === z);
    renderDetail();
  }

  function flash(m) {
    const n = nodes[m.zone]; if (!n || !n.dots[m.metric]) return;
    n.dots[m.metric].forEach(d => { d.setAttribute('opacity', 1); setTimeout(() => d.setAttribute('opacity', 0.35), 350); });
  }

  function updateZone(z) {
    const n = nodes[z]; if (!n) return;
    const status = worstStatus(z), color = STATUS_COLOR[status];
    n.halo.setAttribute('fill', color); n.ring.setAttribute('stroke', color);
    n.halo.setAttribute('class', 'halo' + (status === 'normal' ? '' : ' pulse'));
    const l = App.latest[z], t = l.temperature, a = l.aqi;
    n.info.textContent = (t ? fmtNum(t.avg) + ' C' : '--') + '  |  ' + (a ? 'AQI ' + fmtNum(a.avg, 0) : '--');
  }

  function renderScore() {
    let penalty = 0, count = 0;
    for (const z in App.latest) for (const m in App.latest[z]) { penalty += STATUS_RANK[App.latest[z][m].status]; count++; }
    if (!count) { $('healthscore').textContent = ''; return; }
    const score = Math.round(100 * (1 - penalty / (count * 2)));
    $('healthscore').textContent = 'City health score: ' + score + '%';
  }

  function renderDetail() {
    const box = $('zonedetail'); if (!selected) return; clear(box);
    const z = selected;
    box.appendChild(el('h2', '', 'Zone details'));
    box.appendChild(el('div', 'zonename', App.cfg.zones[z]));
    const g = App.cfg.geo[z];
    box.appendChild(el('div', 'zoneid', 'Zone ' + z + '  |  approx. ' + g.lat.toFixed(3) + ' N, ' + g.lon.toFixed(3) + ' E'));
    const fog = App.stats && App.stats.fog ? (App.stats.fog[z] ? 'online' : 'OFFLINE') : '...';
    const r0 = el('div', 'row'); r0.appendChild(el('span', '', 'Fog node ' + z)); r0.appendChild(el('b', '', fog)); box.appendChild(r0);
    const tb = el('table'); const head = el('tr');
    ['Sensor', 'Now', 'In 30 s', 'Status'].forEach(h => head.appendChild(el('th', '', h))); tb.appendChild(head);
    for (const m in App.cfg.metrics) {
      const cfg = App.cfg.metrics[m], cur = App.latest[z][m], f = App.forecast[z][m], tr = el('tr');
      tr.appendChild(el('td', '', cfg.label));
      tr.appendChild(el('td', '', cur ? fmtNum(cur.avg) + ' ' + cfg.unit : '--'));
      const ftd = el('td', '', f ? fmtNum(f.predicted, 0) : '--'); if (f && f.will_breach) ftd.className = 'risk'; tr.appendChild(ftd);
      const st = el('td'); if (cur) st.appendChild(el('span', 'pill ' + cur.status, cur.status.toUpperCase())); else st.textContent = '--'; tr.appendChild(st);
      tb.appendChild(tr);
    }
    const wrap = el('div', 'wrap'); wrap.appendChild(tb); box.appendChild(wrap);
    box.appendChild(el('h2', '', 'Recent alerts in this zone')); box.lastChild.style.marginTop = '16px';
    const ul = el('ul', 'feed'); const list = App.alerts.filter(a => a.zone === z).slice(0, 6);
    if (!list.length) ul.appendChild(el('li', 'empty', 'No alerts for this zone.'));
    list.forEach(a => {
      const li = el('li'); li.appendChild(el('div', 'bar ' + a.level));
      const body = el('div'); body.appendChild(el('div', '', a.message)); body.appendChild(el('time', '', fmtTime(a.ts)));
      li.appendChild(body); ul.appendChild(li);
    });
    box.appendChild(ul);
  }

  App.pages.map = {
    init() { build(); },
    show() { for (const z in nodes) updateZone(z); renderScore(); renderDetail(); },
  };
  App.on('telemetry', m => { updateZone(m.zone); flash(m); });
  App.on('alert', () => { if (App.current === 'map') renderDetail(); });
  App.on('tick', () => { if (App.current === 'map') { renderScore(); renderDetail(); } });
})();

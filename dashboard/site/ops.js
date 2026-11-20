'use strict';
/* Operations page: service health, throughput, Kafka topics and consumer lag, processor load. */
(function () {
  let healthTimer = null, kafkaTimer = null;
  const LAYERS = [['edge', 'Edge'], ['fog', 'Fog'], ['cloud', 'Cloud'], ['data', 'Data stores']];

  async function loadHealth() {
    try {
      const items = await getJSON('/api/health');
      const box = $('o-health'); clear(box);
      LAYERS.forEach(([key, title]) => {
        const rows = items.filter(i => i.layer === key); if (!rows.length) return;
        box.appendChild(el('div', 'layer', title));
        rows.forEach(i => {
          const r = el('div', 'hrow'); r.appendChild(el('span', 'sd' + (i.status === 'ok' ? '' : ' down')));
          r.appendChild(el('span', 'nm', i.name)); r.appendChild(el('span', 'dt', i.status === 'ok' ? (i.detail || 'ok') : 'DOWN'));
          box.appendChild(r);
        });
      });
    } catch (e) { /* ignore */ }
  }

  async function loadKafka() {
    try {
      const k = await getJSON('/api/kafka'); const box = $('o-kafka'); clear(box);
      if (!k.ok) { box.appendChild(el('div', 'empty', 'Kafka metrics unavailable right now: ' + (k.error || 'unknown error'))); return; }
      const t1 = el('table'), h1 = el('tr'); ['Topic', 'Partitions', 'Messages'].forEach(x => h1.appendChild(el('th', '', x))); t1.appendChild(h1);
      k.topics.forEach(t => { const r = el('tr'); [t.topic, t.partitions, t.messages].forEach(v => r.appendChild(el('td', '', String(v)))); t1.appendChild(r); });
      const w1 = el('div', 'wrap'); w1.appendChild(t1); box.appendChild(w1);
      box.appendChild(el('div', 'layer', 'Consumer groups (lag = messages waiting)'));
      const t2 = el('table'), h2 = el('tr'); ['Group', 'Topic', 'Lag'].forEach(x => h2.appendChild(el('th', '', x))); t2.appendChild(h2);
      k.groups.forEach(g => { const r = el('tr'); [g.group, g.topic, g.lag === null ? 'n/a' : g.lag].forEach(v => r.appendChild(el('td', '', String(v)))); t2.appendChild(r); });
      const w2 = el('div', 'wrap'); w2.appendChild(t2); box.appendChild(w2);
    } catch (e) { /* ignore */ }
  }

  function renderProcs() {
    const box = $('o-procs'); clear(box);
    const s = App.stats; if (!s) return;
    const bars = el('div', 'bars'); const max = Math.max(1, ...s.processors.map(p => p.processed));
    if (!s.processors.length) box.appendChild(el('div', 'empty', 'No processors running.'));
    s.processors.forEach((p, i) => {
      const r = el('div', 'brow'); r.appendChild(el('span', 'lab', 'processor ' + (i + 1) + ' (' + p.id.slice(0, 6) + ')'));
      const track = el('div', 'track'), fill = el('div', 'fill'); fill.style.width = Math.round(100 * p.processed / max) + '%';
      track.appendChild(fill); r.appendChild(track); r.appendChild(el('span', '', p.processed + ' msgs'));
      bars.appendChild(r);
    });
    box.appendChild(bars);
  }

  function drawThroughput() {
    if (App.current !== 'ops') return;
    const now = Date.now(); const raw = App.rates.map(r => ({ t: r.t, v: r.raw })), agg = App.rates.map(r => ({ t: r.t, v: r.agg }));
    const top = Math.max(10, ...raw.map(p => p.v));
    Chart.draw($('ochart'), {
      t0: now - 180000, t1: now, y0: 0, y1: Math.ceil(top * 1.15 / 5) * 5,
      series: [{ color: '#22d3ee', pts: raw, width: 2 }, { color: '#fb923c', pts: agg, width: 2 }],
    });
  }

  App.pages.ops = {
    show() {
      loadHealth(); loadKafka(); renderProcs(); drawThroughput();
      clearInterval(healthTimer); clearInterval(kafkaTimer);
      healthTimer = setInterval(() => { if (App.current === 'ops') loadHealth(); else clearInterval(healthTimer); }, 3000);
      kafkaTimer = setInterval(() => { if (App.current === 'ops') loadKafka(); else clearInterval(kafkaTimer); }, 5000);
    },
  };
  App.on('stats', () => { if (App.current === 'ops') renderProcs(); });
  App.on('tick', drawThroughput);
})();

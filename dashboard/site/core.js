'use strict';
/* CityPulse dashboard - shared core: state, API helpers, WebSocket, router, chart helper. */
const App = {
  cfg: null, latest: {}, series: {}, forecast: {}, alerts: [], stats: null, rates: [],
  listeners: {}, pages: {}, current: 'overview', prevStats: null, ws: null, bannerTimer: null,
};
const ZONE_COLORS = { A: '#22d3ee', B: '#a78bfa', C: '#fb923c' };
const STATUS_RANK = { normal: 0, warning: 1, critical: 2 };
const STATUS_COLOR = { normal: '#34d399', warning: '#fbbf24', critical: '#f87171' };

const $ = id => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString(); }
function fmtNum(v, d) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(d === undefined ? 1 : d) : '--'; }
function option(value, text) { const o = el('option', '', text); o.value = value; return o; }

function authHeaders(extra) {
  const token = sessionStorage.getItem('citypulse_admin_token');
  return Object.assign({}, extra || {}, token ? { Authorization: 'Bearer ' + token } : {});
}
function handleUnauthorized(response) {
  if (response.status === 401 && !location.pathname.endsWith('/login.html')) {
    sessionStorage.removeItem('citypulse_admin_token');
    location.replace('/login.html');
  }
}
async function getJSON(url) {
  const r = await fetch(url, { headers: authHeaders() });
  handleUnauthorized(r);
  if (!r.ok) throw new Error(url + ' -> ' + r.status);
  return r.json();
}
async function sendJSON(url, method, body) {
  const r = await fetch(url, {
    method, headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  handleUnauthorized(r);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    let msg = 'Request failed (' + r.status + ')';
    if (typeof data.detail === 'string') msg = data.detail;
    else if (Array.isArray(data.detail) && data.detail[0]) msg = 'Invalid input: ' + (data.detail[0].msg || '');
    throw new Error(msg);
  }
  return data;
}

/* ---------- tiny event bus ---------- */
App.on = (evt, fn) => { (App.listeners[evt] = App.listeners[evt] || []).push(fn); };
App.emit = (evt, data) => {
  (App.listeners[evt] || []).forEach(fn => { try { fn(data); } catch (e) { console.error('handler error for ' + evt, e); } });
};

/* ---------- banner ---------- */
App.banner = (text, level, ms) => {
  const b = $('banner');
  b.textContent = text; b.className = level || ''; b.style.display = 'block';
  clearTimeout(App.bannerTimer);
  App.bannerTimer = setTimeout(() => { b.style.display = 'none'; }, ms || 7000);
};

/* ---------- chart helper (plain canvas, no libraries) ---------- */
const Chart = {
  prep(c) {
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return null;
    const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (c.width !== pw || c.height !== ph) { c.width = pw; c.height = ph; }
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  },
  spark(c, pts, y0, y1, color) {
    const p = this.prep(c); if (!p) return;
    const data = pts.slice(-40); if (data.length < 2) return;
    const { ctx, w, h } = p;
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
    data.forEach((d, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - Math.max(0, Math.min(1, (d.v - y0) / (y1 - y0))) * (h - 4) - 2;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
  },
  /* o: {t0,t1,y0,y1, series:[{color,pts:[{t,v}],width,dash}], bands:[{color,pts:[{t,lo,hi}]}],
         hlines:[{v,color}], vlines:[{t,color}], xfmt(t), yfmt(v)} - times in ms */
  draw(c, o) {
    const p = this.prep(c); if (!p) return;
    const { ctx, w, h } = p;
    const padL = 48, padR = 14, padT = 10, padB = 24, pw = w - padL - padR, ph = h - padT - padB;
    const X = t => padL + ((t - o.t0) / (o.t1 - o.t0)) * pw;
    const Y = v => padT + ph - Math.max(0, Math.min(1, (v - o.y0) / (o.y1 - o.y0))) * ph;
    const xfmt = o.xfmt || (t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    const yfmt = o.yfmt || (v => String(Math.round(v)));
    ctx.font = '11px system-ui, sans-serif'; ctx.lineWidth = 1;
    ctx.strokeStyle = '#223052'; ctx.fillStyle = '#8b97b5';
    for (let i = 0; i <= 4; i++) {
      const v = o.y0 + (i / 4) * (o.y1 - o.y0), y = Y(v);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
      ctx.fillText(yfmt(v), 4, y + 4);
    }
    for (let i = 0; i <= 3; i++) {
      const t = o.t0 + (i / 3) * (o.t1 - o.t0);
      const txt = xfmt(t), tw = ctx.measureText(txt).width;
      ctx.fillText(txt, Math.min(w - tw - 2, Math.max(padL - 10, X(t) - tw / 2)), h - 6);
    }
    (o.bands || []).forEach(b => {
      const pts = b.pts.filter(q => q.t >= o.t0 && q.t <= o.t1); if (pts.length < 2) return;
      ctx.fillStyle = b.color; ctx.globalAlpha = 0.18; ctx.beginPath();
      pts.forEach((q, i) => { if (i) ctx.lineTo(X(q.t), Y(q.hi)); else ctx.moveTo(X(q.t), Y(q.hi)); });
      for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(X(pts[i].t), Y(pts[i].lo));
      ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
    });
    (o.hlines || []).forEach(l => {
      if (l.v < o.y0 || l.v > o.y1) return;
      ctx.strokeStyle = l.color; ctx.setLineDash([6, 4]); ctx.beginPath();
      ctx.moveTo(padL, Y(l.v)); ctx.lineTo(w - padR, Y(l.v)); ctx.stroke(); ctx.setLineDash([]);
    });
    (o.vlines || []).forEach(l => {
      ctx.strokeStyle = l.color; ctx.setLineDash([2, 4]); ctx.beginPath();
      ctx.moveTo(X(l.t), padT); ctx.lineTo(X(l.t), padT + ph); ctx.stroke(); ctx.setLineDash([]);
    });
    let drawn = 0;
    (o.series || []).forEach(s => {
      const pts = s.pts.filter(q => q.t >= o.t0 && q.t <= o.t1); if (pts.length < 2) return;
      drawn++;
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 2; ctx.setLineDash(s.dash || []); ctx.beginPath();
      pts.forEach((q, i) => { if (i) ctx.lineTo(X(q.t), Y(q.v)); else ctx.moveTo(X(q.t), Y(q.v)); });
      ctx.stroke(); ctx.setLineDash([]);
    });
    if (!drawn) { ctx.fillStyle = '#8b97b5'; ctx.fillText('Collecting data...', padL + 12, padT + 24); }
  },
};

/* ---------- data handling ---------- */
function applyTelemetry(m) {
  if (!App.latest[m.zone] || !App.series[m.metric]) return;
  App.latest[m.zone][m.metric] = m;
  const arr = App.series[m.metric][m.zone];
  arr.push({ t: m.ts * 1000, v: m.avg });
  if (arr.length > 400) arr.shift();
  App.emit('telemetry', m);
}
function applyAlert(a, live) {
  App.alerts.unshift(a);
  if (App.alerts.length > 120) App.alerts.pop();
  App.emit('alert', { alert: a, live });
  if (live && a.level !== 'info') {
    const prefix = { critical: 'CRITICAL: ', warning: 'WARNING: ', predicted: 'EARLY WARNING: ' }[a.level] || '';
    App.banner(prefix + a.message, a.level);
  }
}
function worstStatus(zone) {
  let worst = 'normal';
  for (const m in App.latest[zone]) {
    const s = App.latest[zone][m].status;
    if (STATUS_RANK[s] > STATUS_RANK[worst]) worst = s;
  }
  return worst;
}

async function pollStats() {
  try {
    const s = await getJSON('/api/stats'); const now = Date.now();
    let rawRate = null, aggRate = null;
    if (App.prevStats) {
      const dt = (now - App.prevStats.t) / 1000;
      if (dt > 0) {
        rawRate = Math.max(0, (s.raw_msgs - App.prevStats.s.raw_msgs) / dt);
        aggRate = Math.max(0, (s.agg_msgs - App.prevStats.s.agg_msgs) / dt);
        App.rates.push({ t: now, raw: rawRate, agg: aggRate });
        if (App.rates.length > 100) App.rates.shift();
      }
    }
    App.prevStats = { s, t: now };
    App.stats = s; s.rawRate = rawRate; s.aggRate = aggRate;
    App.emit('stats', s);
  } catch (e) { /* backend not ready */ }
}
async function pollForecast() {
  try {
    const list = await getJSON('/api/forecast');
    list.forEach(f => { if (App.forecast[f.zone]) App.forecast[f.zone][f.metric] = f; });
    App.emit('forecast', list);
  } catch (e) { /* ignore */ }
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  const token = sessionStorage.getItem('citypulse_admin_token') || '';
  const ws = new WebSocket(proto + location.host + '/ws?token=' + encodeURIComponent(token));
  App.ws = ws;
  ws.onopen = () => { $('wsdot').className = 'dot on'; $('wstext').textContent = 'Live'; };
  ws.onclose = () => { $('wsdot').className = 'dot'; $('wstext').textContent = 'Reconnecting...'; setTimeout(connectWS, 2000); };
  ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
  ws.onmessage = ev => {
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === 'telemetry') applyTelemetry(m);
    else if (m.type === 'alert') applyAlert(m, true);
  };
}

/* ---------- router ---------- */
function showPage(name) {
  if (!App.pages[name]) name = 'overview';
  App.current = name;
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
  document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('active', b.dataset.page === name));
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  const page = App.pages[name];
  if (page && page.show) { try { page.show(); } catch (e) { console.error('show ' + name, e); } }
}
function setupRouter() {
  document.querySelectorAll('#nav button').forEach(b => { b.onclick = () => showPage(b.dataset.page); });
  window.addEventListener('hashchange', () => showPage(location.hash.replace('#', '')));
}

/* ---------- start ---------- */
App.start = async function () {
  try {
    const token = sessionStorage.getItem('citypulse_admin_token');
    if (!token) { location.replace('/login.html'); return; }
    const authCheck = await fetch('/api/auth/me', { headers: authHeaders() });
    if (!authCheck.ok) { sessionStorage.removeItem('citypulse_admin_token'); location.replace('/login.html'); return; }
    const admin = await authCheck.json();
    const userNode = $('admin-user'); if (userNode) userNode.textContent = admin.username;
    const logout = $('logout-btn');
    if (logout) logout.onclick = () => { sessionStorage.removeItem('citypulse_admin_token'); location.replace('/login.html'); };
    App.cfg = await getJSON('/api/config');
    for (const z in App.cfg.zones) { App.latest[z] = {}; App.forecast[z] = {}; }
    for (const m in App.cfg.metrics) { App.series[m] = {}; for (const z in App.cfg.zones) App.series[m][z] = []; }
    if (!App.inited) {
      for (const name in App.pages) { if (App.pages[name].init) App.pages[name].init(); }
      App.inited = true;
    }
    await Promise.all(Object.keys(App.cfg.metrics).map(async m => {
      const h = await getJSON('/api/history?metric=' + m + '&minutes=3');
      for (const z in h) if (App.series[m][z]) App.series[m][z] = h[z].map(p => ({ t: p.t * 1000, v: p.v }));
    }));
    (await getJSON('/api/state')).forEach(applyTelemetry);
    (await getJSON('/api/alerts?limit=60')).reverse().forEach(a => applyAlert(a, false));
    setupRouter();
    connectWS();
    pollStats(); pollForecast();
    setInterval(pollStats, 2000);
    setInterval(pollForecast, 2000);
    setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString(); App.emit('tick'); }, 1000);
    window.addEventListener('resize', () => App.emit('tick'));
    showPage(location.hash.replace('#', '') || 'overview');
  } catch (e) {
    console.error(e);
    $('wstext').textContent = 'Waiting for backend...';
    setTimeout(App.start, 3000);
  }
};

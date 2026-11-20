'use strict';
/* Alerts & Rules page: create/toggle/delete rules (MongoDB), alert history, notification log. */
(function () {
  let timer = null;

  function init() {
    const z = $('r-zone'), m = $('r-metric'); clear(z); clear(m);
    z.appendChild(option('*', 'Any zone'));
    for (const k in App.cfg.zones) z.appendChild(option(k, App.cfg.zones[k]));
    for (const k in App.cfg.metrics) m.appendChild(option(k, App.cfg.metrics[k].label + ' (' + App.cfg.metrics[k].unit + ')'));
    $('r-add').onclick = addRule;
    $('h-level').onchange = renderHistory;
  }

  function msg(text, isErr) { const m = $('r-msg'); m.textContent = text; m.className = 'formmsg' + (isErr ? ' err' : ''); }

  async function addRule() {
    const channels = [];
    if ($('r-email').checked) channels.push('email');
    if ($('r-sms').checked) channels.push('sms');
    if ($('r-tg').checked) channels.push('telegram');
    const thr = parseFloat($('r-thr').value);
    if (!$('r-name').value.trim()) return msg('Please enter a rule name.', true);
    if (!isFinite(thr)) return msg('Please enter a numeric threshold.', true);
    if (!channels.length) return msg('Choose at least one notification channel.', true);
    try {
      await sendJSON('/api/rules', 'POST', {
        name: $('r-name').value.trim(), zone: $('r-zone').value, metric: $('r-metric').value,
        op: $('r-op').value, threshold: thr, severity: $('r-sev').value, channels,
      });
      msg('Rule added. It is evaluated on the next readings.', false);
      $('r-name').value = ''; $('r-thr').value = '';
      loadRules();
    } catch (e) { msg(e.message, true); }
  }

  function condText(r) {
    const zone = r.zone === '*' ? 'Any zone' : (App.cfg.zones[r.zone] || r.zone);
    const cfg = App.cfg.metrics[r.metric] || { label: r.metric, unit: '' };
    return zone + '  |  ' + cfg.label + ' ' + (r.op === '>' ? 'above' : 'below') + ' ' + r.threshold + ' ' + cfg.unit + '  |  ' + r.channels.join(', ');
  }

  async function loadRules() {
    try {
      const rules = await getJSON('/api/rules');
      const ul = $('r-list'); clear(ul);
      if (!rules.length) { ul.appendChild(el('li', 'empty', 'No rules yet. Create one on the left.')); return; }
      rules.forEach(r => {
        const li = el('li', 'rulerow' + (r.enabled ? '' : ' off'));
        const info = el('div', 'info'), title = el('div', 'title', r.name);
        title.appendChild(el('span', 'sev ' + r.severity, r.severity.toUpperCase()));
        info.appendChild(title); info.appendChild(el('div', 'cond', condText(r)));
        const acts = el('div', 'acts');
        const t = el('button', 'smallbtn', r.enabled ? 'Disable' : 'Enable');
        t.onclick = async () => { try { await sendJSON('/api/rules/' + r.id, 'PATCH', { enabled: !r.enabled }); loadRules(); } catch (e) { msg(e.message, true); } };
        const d = el('button', 'smallbtn del', 'Delete');
        d.onclick = async () => { try { await sendJSON('/api/rules/' + r.id, 'DELETE'); loadRules(); } catch (e) { msg(e.message, true); } };
        acts.appendChild(t); acts.appendChild(d);
        li.appendChild(info); li.appendChild(acts); ul.appendChild(li);
      });
    } catch (e) { /* ignore transient errors */ }
  }

  function renderHistory() {
    const ul = $('h-list'); clear(ul);
    const level = $('h-level').value;
    const list = App.alerts.filter(a => !level || a.level === level).slice(0, 60);
    if (!list.length) { ul.appendChild(el('li', 'empty', 'No alerts' + (level ? ' with this level' : ' yet') + '.')); return; }
    list.forEach(a => {
      const li = el('li'); li.appendChild(el('div', 'bar ' + a.level));
      const body = el('div'); body.appendChild(el('div', '', a.message));
      const t = el('time', '', new Date(a.ts * 1000).toLocaleString());
      if (a.source === 'rule') t.appendChild(el('span', 'tag', 'rule'));
      if (a.level === 'predicted') t.appendChild(el('span', 'tag', 'predicted'));
      body.appendChild(t); li.appendChild(body); ul.appendChild(li);
    });
  }

  async function loadNotifications() {
    try {
      const list = await getJSON('/api/notifications?limit=40');
      const ul = $('n-list'); clear(ul);
      if (!list.length) { ul.appendChild(el('li', 'empty', 'No notifications yet.')); return; }
      list.forEach(n => {
        const li = el('li'); li.appendChild(el('div', 'bar ' + (n.level || 'info')));
        const body = el('div'); body.appendChild(el('div', '', n.text));
        const t = el('time', '', new Date(n.ts * 1000).toLocaleTimeString() + '  |  ' + n.channel.toUpperCase() + ' to ' + n.to);
        t.appendChild(el('span', 'tag', n.delivered ? n.note : 'not delivered: ' + n.note));
        body.appendChild(t); li.appendChild(body); ul.appendChild(li);
      });
    } catch (e) { /* ignore */ }
  }

  App.pages.rules = {
    init,
    show() {
      loadRules(); renderHistory(); loadNotifications();
      clearInterval(timer);
      timer = setInterval(() => { if (App.current === 'rules') { loadNotifications(); loadRules(); } else clearInterval(timer); }, 4000);
    },
  };
  App.on('alert', () => { if (App.current === 'rules') { renderHistory(); loadNotifications(); } });
})();

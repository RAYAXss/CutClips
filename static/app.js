// Shared UI: live logs, job panel, toasts. Every server string goes through textContent (no innerHTML).
(function () {
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  window.toast = function (text, level) {
    var box = document.getElementById('toasts');
    if (!box) return;
    var t = el('div', 'toast ' + (level === 'error' ? 'error' : ''));
    t.setAttribute('role', level === 'error' ? 'alert' : 'status');
    t.appendChild(el('span', '', text));
    var x = el('button', 'x', '✕');
    x.setAttribute('aria-label', 'Fermer');
    x.onclick = function () { t.remove(); };
    t.appendChild(x);
    box.appendChild(t);
    setTimeout(function () { t.remove(); }, level === 'error' ? 9000 : 5000);
  };

  // Flash messages rendered by the server
  document.querySelectorAll('[data-flash]').forEach(function (f) {
    toast(f.dataset.flash, f.dataset.level);
  });

  // <dialog> openers: <button data-open="id">
  document.querySelectorAll('[data-open]').forEach(function (b) {
    b.addEventListener('click', function () {
      var d = document.getElementById(b.dataset.open);
      if (b.dataset.user) d.querySelectorAll('[data-fill]').forEach(function (i) {
        if (i.tagName === 'INPUT') i.value = b.dataset.user; else i.textContent = b.dataset.user;
      });
      d.showModal();
    });
  });
  document.querySelectorAll('[data-close]').forEach(function (b) {
    b.addEventListener('click', function () { b.closest('dialog').close(); });
  });
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  var logbox = document.getElementById('logbox');
  var lc = document.getElementById('lc');
  var jobbox = document.getElementById('jobbox');
  var STATUS = { queued: 'en attente', running: 'en cours', done: 'terminé', error: 'erreur' };
  var jobs = {};

  function addLog(d) {
    if (!logbox) return;
    logbox.appendChild(el('div', 'logline ' + (d.level || 'info'), d.text));
    logbox.scrollTop = logbox.scrollHeight;
    if (lc) lc.textContent = logbox.children.length;
  }

  function renderJob(j) {
    if (!jobbox) return;
    var row = document.getElementById('job-' + j.id);
    if (!row) {
      row = el('div', 'job');
      row.id = 'job-' + j.id;
      var top = el('div', 'job-top');
      top.appendChild(el('span', 'job-l'));
      top.appendChild(el('span', 'job-s'));
      row.appendChild(top);
      var bar = el('div', 'bar');
      bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', '100');
      bar.appendChild(el('i'));
      row.appendChild(bar);
      row.appendChild(el('div', 'job-e'));
      jobbox.prepend(row);
      var empty = document.getElementById('jobempty');
      if (empty) empty.remove();
    }
    row.querySelector('.job-l').textContent = j.label;
    var s = row.querySelector('.job-s');
    s.className = 'job-s ' + j.status;
    s.textContent = j.status === 'running' ? j.progress + ' %' : STATUS[j.status];
    row.querySelector('.bar').setAttribute('aria-valuenow', j.progress);
    row.querySelector('.bar i').style.width = (j.status === 'done' ? 100 : j.progress) + '%';
    row.querySelector('.job-e').textContent = j.error || '';
    var prev = jobs[j.id];
    if (prev && prev !== j.status) {
      if (j.status === 'done') toast(j.label + ' : terminé', 'success');
      if (j.status === 'error') toast(j.label + ' : ' + (j.error || 'erreur'), 'error');
    }
    jobs[j.id] = j.status;
  }

  (window.INITIAL_JOBS || []).slice().reverse().forEach(renderJob);
  if (logbox) logbox.scrollTop = logbox.scrollHeight;

  if (window.io) {
    var socket = io();
    socket.on('new_log', addLog);
    socket.on('job_update', renderJob);
    socket.on('clear_logs', function () { if (logbox) logbox.textContent = ''; if (lc) lc.textContent = '0'; });
  }

  // Category chips (creation page)
  var cat = document.getElementById('cat-v');
  document.querySelectorAll('.chip[data-cat]').forEach(function (c) {
    c.addEventListener('click', function () {
      document.querySelectorAll('.chip[data-cat]').forEach(function (o) { o.setAttribute('aria-pressed', 'false'); });
      c.setAttribute('aria-pressed', 'true');
      cat.value = c.dataset.cat;
    });
  });
  if (cat) cat.addEventListener('input', function () {
    document.querySelectorAll('.chip[data-cat]').forEach(function (o) {
      o.setAttribute('aria-pressed', String(o.dataset.cat === cat.value));
    });
  });

  // Range outputs: <input type=range data-out="id" data-unit="...">
  document.querySelectorAll('input[type=range][data-out]').forEach(function (r) {
    var out = document.getElementById(r.dataset.out);
    function upd() { out.textContent = r.value; }
    r.addEventListener('input', upd); upd();
  });
})();

/* Contract2BPMN front end. Talks to Python through pywebview (desktop) or HTTP (browser test mode). */
(function () {
  const $ = (id) => document.getElementById(id);
  const modeler = new BpmnJS({ container: '#canvas' });
  let currentXml = null;

  const EXAMPLE =
    'Lotus Inn (hereinafter the Provider) shall deliver the booked conference room with working video equipment. ' +
    'If the Provider cannot deliver the room as booked, it shall offer an equivalent room in the same building ' +
    'within 2 hours. Failing which, the Provider shall arrange a room at a partner hotel at no extra cost. ' +
    'If neither remedy is possible, the client is entitled to a refund of 100% of the booking fee.';

  // ---- bridge: desktop (pywebview) or browser test mode (HTTP) ----
  function call(name, ...args) {
    if (window.pywebview && window.pywebview.api) return window.pywebview.api[name](...args);
    const body = name === 'convert' ? { text: args[0] } : {};
    return fetch('/api/' + name, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) }).then((r) => r.json());
  }
  const desktop = () => !!(window.pywebview && window.pywebview.api);

  // ---- status polling until the model is ready ----
  function setStatus(cls, text) { const s = $('status'); s.className = 'pill ' + cls; s.textContent = text; }
  async function poll() {
    try {
      const st = await call('get_status');
      if (st.status === 'ready') { setStatus('ok', 'Model ready'); $('btn-convert').disabled = false; return; }
      if (st.status.startsWith('error')) { setStatus('err', 'Model error'); showError(st.status); return; }
    } catch (e) { /* bridge not ready yet */ }
    setTimeout(poll, 800);
  }
  window.addEventListener('pywebviewready', poll);
  setTimeout(poll, 300);

  // ---- rendering helpers ----
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function showError(msg) {
    $('result').innerHTML = '<h3>What the tool understood</h3><div class="error">' + esc(msg) + '</div>';
  }
  function showResult(r) {
    let h = '<h3>What the tool understood</h3>';
    h += '<h4>Parties</h4>' + r.parties.map((p) =>
      '<div><span class="tag ' + esc(p.type) + '">' + esc(p.type) + '</span><b>' + esc(p.name) + '</b>' +
      (p.aliases.length ? ' <span class="hint">also: ' + p.aliases.map(esc).join(', ') + '</span>' : '') + '</div>').join('');
    r.rules.forEach((ru, i) => {
      h += '<h4>Rule ' + (i + 1) + ': if ' + esc(ru.trigger) + '</h4><ol class="chain">' + ru.steps.map((s) =>
        '<li><b>' + esc(s.actor) + '</b> <span class="mod">' + esc(s.modality) + '</span> ' + esc(s.remedy) + '</li>').join('') +
        '</ol><div class="hint">Each step applies only if the previous one fails.</div>';
    });
    if (r.warnings && r.warnings.length) h += '<h4>Warnings (please check)</h4>' + r.warnings.map((w) => '<div class="warn">' + esc(w) + '</div>').join('');
    h += '<p class="hint">' + (r.valid ? 'Valid BPMN 2.0 file' : 'The file did not pass the BPMN check') + ', ' + r.seconds + ' s.</p>';
    $('result').innerHTML = h;
  }

  // fit the diagram and move it right, so the tool palette does not cover the start event
  function fitView() { const c = modeler.get('canvas'); c.zoom('fit-viewport'); c.scroll({ dx: 70, dy: 0 }); }

  // ---- actions ----
  $('btn-example').onclick = () => { $('contract').value = EXAMPLE; };
  $('btn-clear').onclick = () => { $('contract').value = ''; };
  $('btn-open').onclick = async () => {
    if (!desktop()) { alert('Open text is available in the desktop app.'); return; }
    const r = await call('open_text'); if (r.ok) $('contract').value = r.text;
  };
  $('btn-convert').onclick = async () => {
    const b = $('btn-convert'); b.disabled = true; b.innerHTML = '<span class="spinner"></span>Reading the contract&hellip;';
    try {
      const r = await call('convert', $('contract').value);
      if (!r.ok) { showError(r.error); return; }
      currentXml = r.xml;
      await modeler.importXML(r.xml);
      fitView();
      $('empty').style.display = 'none'; $('btn-save').disabled = false; $('btn-fit').disabled = false;
      showResult(r);
    } catch (e) { showError(String(e)); }
    finally { b.disabled = false; b.textContent = 'Draw workflow'; }
  };
  $('btn-fit').onclick = () => fitView();
  $('btn-save').onclick = async () => {
    const { xml } = await modeler.saveXML({ format: true });
    if (desktop()) { const r = await call('save_bpmn', xml, 'workflow.bpmn'); if (r.ok) setStatus('ok', 'Saved: ' + r.path); return; }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([xml], { type: 'application/xml' })); a.download = 'workflow.bpmn'; a.click();
  };
})();

'use strict';
(() => {
  const app = document.getElementById('scanner-app');
  if (!app) return;
  const byId = id => document.getElementById(id);
  const input = byId('scan-code');
  const storageKey = `pending-stock-scans:${app.dataset.actor}`;
  let saved = [];
  try { const raw = JSON.parse(sessionStorage.getItem(storageKey) || '[]'); if (Array.isArray(raw)) saved = raw.filter(x => x && typeof x.codice === 'string' && typeof x.richiesta_id === 'string'); } catch { /* storage may be unavailable */ }
  const queue = saved;
  function persist() { try { sessionStorage.setItem(storageKey, JSON.stringify(queue)); } catch { /* memory queue still works */ } }
  let mode = 'uscita', busy = false, paused = false, uncertain = false;
  const modes = [...app.querySelectorAll('[data-mode]')];
  const quantityText = n => `${n} ${n === 1 ? 'pezzo registrato' : 'pezzi registrati'}`;
  const uid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const h = [...bytes].map(x => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
  };
  function controls() {
    persist();
    input.disabled = paused;
    byId('scan-form').querySelector('button').disabled = paused;
    modes.forEach(button => { button.disabled = paused; });
    app.querySelectorAll('#scan-recovery button').forEach(button => { button.disabled = busy || button.dataset.unavailable === 'true'; });
    byId('scan-queue').textContent = paused ? `In pausa: completa la scansione. ${queue.length} in attesa.`
      : queue.length ? `${queue.length} scansioni in elaborazione…` : 'Pronto per la scansione.';
  }
  function feedback(message, error = false) {
    byId('scan-feedback').className = `alert-custom alert-${error ? 'danger' : 'success'}`;
    byId('scan-feedback').textContent = message;
  }
  function recovery(data) {
    const body = queue[0];
    byId('scan-recovery').hidden = false;
    byId('unknown-recovery').hidden = data.status !== 'sconosciuto';
    byId('zero-recovery').hidden = data.status !== 'conferma_giacenza';
    byId('error-recovery').hidden = ['sconosciuto', 'conferma_giacenza'].includes(data.status);
    byId('recovery-title').textContent = data.status === 'sconosciuto' ? 'Codice non registrato'
      : data.status === 'conferma_giacenza' ? 'Conferma il prodotto presente' : 'Scansione in attesa';
    byId('recovery-context').textContent = `${body.modalita === 'uscita' ? 'Uscita −1' : 'Rifornimento +1'} · Codice ${body.codice}${data.prodotto ? ' · ' + data.prodotto.nome : ''}`;
    byId('quick-submit').textContent = body.modalita === 'uscita' ? 'Crea prodotto e registra uscita' : 'Crea prodotto e registra rifornimento';
    byId('recovery-error').textContent = data.message || 'Riprova questa scansione.';
    byId('reset-recovery').hidden = !body.azione || body.azione === 'normale';
    if (data.prodotto) body.prodotto_id = data.prodotto.id;
    if (data.status === 'sconosciuto') {
      byId('scan-search-results').replaceChildren();
      byId('scan-search').focus();
    }
    if (data.status === 'conferma_giacenza') byId('confirm-present').focus();
  }
  function log(data) {
    byId('scan-log-empty')?.remove();
    const item = document.createElement('li');
    const badge = document.createElement('strong');
    badge.textContent = data.modalita === 'uscita' ? '−1 Uscita' : '+1 Rifornimento';
    const text = document.createElement('span');
    text.textContent = `${data.prodotto.nome} · ${quantityText(data.prodotto.quantita)}${data.prodotto.da_verificare ? ' · Giacenza da verificare' : ''}`;
    item.append(badge, text); byId('scan-log').prepend(item);
    while (byId('scan-log').children.length > 20) byId('scan-log').lastElementChild.remove();
  }
  async function processQueue() {
    if (busy || paused || !queue.length) { controls(); return; }
    busy = true; controls();
    try {
      const response = await fetch(app.dataset.endpoint, {
        method:'POST', headers:{'Content-Type':'application/json', 'X-CSRFToken':byId('scan-csrf').value},
        body:JSON.stringify(queue[0])
      });
      const data = await response.json();
      uncertain = false;
      if (response.ok && data.status === 'ok') {
        queue.shift(); log(data);
        feedback(`${data.prodotto.nome}: ${data.message} ${quantityText(data.prodotto.quantita)}.`);
        byId('scan-recovery').hidden = true;
        byId('quick-product-form').reset(); byId('quick-create').open = false;
      } else { paused = true; recovery(data); }
    } catch {
      uncertain = true; paused = true;
      recovery({status:'errore', message:'Risposta non ricevuta: la scansione potrebbe essere già registrata. Premi “Riprova questa scansione”, senza scansionare di nuovo il pezzo.'});
    } finally {
      busy = false; controls();
      if (!paused) { if (document.activeElement === document.body) input.focus(); processQueue(); }
    }
  }
  function resume(extra = {}) {
    if (busy || !queue.length) return;
    Object.assign(queue[0], extra); persist(); paused = false;
    byId('scan-recovery').hidden = true;
    input.focus(); processQueue();
  }
  modes.forEach(button => button.addEventListener('click', () => {
    mode = button.dataset.mode;
    modes.forEach(b => { const selected = b === button; b.classList.toggle('selected', selected); b.setAttribute('aria-pressed', String(selected)); });
    byId('scan-mode-status').textContent = mode === 'uscita'
      ? 'Modalità attiva: Uscita · ogni codice + Invio registra un pezzo in uscita.'
      : 'Modalità attiva: Rifornimento · ogni codice + Invio aggiunge un pezzo al magazzino.';
    input.focus();
  }));
  byId('scan-form').addEventListener('submit', event => {
    event.preventDefault(); if (paused) return;
    const code = input.value.trim(); if (!code) return;
    queue.push({codice:code, modalita:mode, richiesta_id:uid()});
    input.value = ''; input.focus(); processQueue();
  });
  byId('scan-search-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    const term = byId('scan-search').value.trim(); if (!term) return;
    const results = byId('scan-search-results'); results.textContent = 'Ricerca…';
    try {
      const response = await fetch(`${app.dataset.search}?q=${encodeURIComponent(term)}`);
      const data = await response.json(); results.replaceChildren();
      if (!data.prodotti.length) results.textContent = 'Nessun prodotto trovato. Puoi registrarlo qui sotto.';
      data.prodotti.forEach(p => {
        const row = document.createElement('div'); row.className = 'scan-search-result';
        const label = document.createElement('span'); label.textContent = `${p.nome}${p.marca ? ' · ' + p.marca : ''} · ${quantityText(p.quantita)}`;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-ghost';
        button.textContent = p.codice ? 'Codice già presente' : 'Associa e registra'; button.disabled = Boolean(p.codice); button.dataset.unavailable = String(Boolean(p.codice));
        button.addEventListener('click', () => resume({azione:'associa', prodotto_id:p.id}));
        row.append(label, button); results.append(row);
      });
    } catch { results.textContent = 'Ricerca non disponibile. Riprova o verifica l’accesso al gestionale.'; }
  });
  byId('quick-product-form').addEventListener('submit', event => {
    event.preventDefault(); if (!event.target.reportValidity()) return;
    const stock = byId('quick-stock').value.trim();
    resume({azione:'crea', nome:byId('quick-name').value.trim(), quantita_iniziale:stock === '' ? null : Number(stock)});
  });
  byId('confirm-present').addEventListener('click', () => resume({azione:queue[0].azione || 'conferma', conferma_zero:true}));
  byId('retry-scan').addEventListener('click', () => resume());
  byId('reset-recovery').addEventListener('click', () => {
    const b=queue[0]; queue[0]={codice:b.codice, modalita:b.modalita, richiesta_id:b.richiesta_id}; resume();
  });
  byId('skip-scan').addEventListener('click', () => {
    if (uncertain && !window.confirm('La richiesta potrebbe essere già registrata. Controlla lo storico prima di scansionare nuovamente questo pezzo. Saltare la richiesta in attesa?')) return;
    queue.shift(); paused = false; uncertain = false;
    byId('scan-recovery').hidden = true; feedback('Scansione saltata.'); controls(); input.focus(); processQueue();
  });
  controls();
  if (queue.length) {
    paused = true; uncertain = true;
    recovery({status:'errore', message:'Ci sono scansioni in attesa dalla sessione precedente. Premi “Riprova questa scansione”: se era già registrata, non verrà conteggiata due volte.'});
    controls();
  }
})();

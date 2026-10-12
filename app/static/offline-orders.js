// Explicitly reviewed device drafts; only the authenticated account can submit its own queue.
(() => {
  const form = document.querySelector('[data-offline-order]');
  if (!form) return;
  const queueKey = `aapoorti-order-drafts-${form.dataset.userId}`;
  const panel = document.createElement('section');
  panel.className = 'card ops-panel offline-drafts';
  panel.hidden = true;
  document.querySelector('main').prepend(panel);
  const read = () => { try { return JSON.parse(localStorage.getItem(queueKey) || '[]'); } catch { return []; } };
  function save(rows) { localStorage.setItem(queueKey, JSON.stringify(rows)); render(); }
  const stopLoader = () => { const loader = document.getElementById('app-loader'); if (loader) loader.hidden = true; };
  function render() {
    const rows = read(); panel.replaceChildren(); panel.hidden = !rows.length;
    if (!rows.length) return;
    const heading = document.createElement('h2'); heading.textContent = 'Orders saved on this device'; panel.append(heading);
    const note = document.createElement('p'); note.textContent = 'These are unsubmitted drafts. Reconnect, review and submit. Prices and wallet balance are checked by the server at submission.'; panel.append(note);
    rows.forEach(row => {
      const box = document.createElement('article'); box.className = 'ops-row';
      const label = document.createElement('b'); label.textContent = `${row.customer_name} · ${row.customer_phone} · ${row.status_mode}`; box.append(label);
      const submit = document.createElement('button'); submit.type = 'button'; submit.textContent = 'Submit saved order';
      submit.onclick = async () => {
        submit.disabled = true;
        try {
          const data = new URLSearchParams(row); data.set('csrf_token', form.elements.csrf_token.value);
          const result = await fetch('/franchise/orders', {method:'POST', body:data});
          if (!result.ok || !new URL(result.url).searchParams.has('created')) throw new Error('Sign in again or check the order details. This draft has been retained.');
          save(read().filter(r => r.request_key !== row.request_key)); location.assign(result.url);
        } catch (error) { note.textContent = error.message; submit.disabled = false; }
      };
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Discard draft';
      remove.onclick = () => save(read().filter(r => r.request_key !== row.request_key));
      box.append(submit, remove); panel.append(box);
    });
  }
  form.addEventListener('submit', event => {
    if (event.defaultPrevented || navigator.onLine) return;
    event.preventDefault();
    const draft = Object.fromEntries(new FormData(form)); delete draft.csrf_token;
    try {
      const rows = read();
      if (!rows.some(r => r.request_key === draft.request_key)) rows.push(draft);
      save(rows); form.elements.request_key.value = crypto.randomUUID();
      if (typeof closeCart === 'function') closeCart();
      panel.scrollIntoView({behavior:'smooth'});
    } catch { alert('Unable to save on this device. Keep this page open and reconnect.'); }
    stopLoader();
  });
  document.querySelector('form[action="/logout"]')?.addEventListener('submit', () => {
    // Drafts deliberately remain scoped to this user for their next login.
    panel.replaceChildren();
  });
  render();
})();

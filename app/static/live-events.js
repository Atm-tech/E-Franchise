(() => {
  if (!document.querySelector('form[action="/logout"]')) return;
  let cursor = 0, dirty = false, initialized = false, timer, source;
  const savedView = sessionStorage.getItem('aapoorti-live-view');
  if (savedView) {
    sessionStorage.removeItem('aapoorti-live-view');
    try {
      const saved = JSON.parse(savedView);
      if (saved.path === location.pathname && saved.view && typeof window.showView === 'function') window.showView(saved.view);
      if (saved.path === location.pathname) window.scrollTo(0, saved.scroll);
    } catch { /* Ignore old view state. */ }
  }
  document.addEventListener('input', () => { dirty = true; }, true);
  document.addEventListener('change', () => { dirty = true; }, true);
  function safeToRefresh() {
    return !dirty && !document.hidden && !document.querySelector('.drawer[aria-hidden="false"]') &&
      !document.activeElement?.matches('input,textarea,select');
  }
  function apply(data) {
    cursor = data.cursor;
    if (data.initial || !initialized) { initialized = true; return; }
    if (!data.events.length) return;
    if (safeToRefresh()) {
      sessionStorage.setItem('aapoorti-live-view', JSON.stringify({path:location.pathname,view:document.querySelector('.app-view.active')?.id,scroll:scrollY}));
      location.reload(); return;
    }
    let notice = document.getElementById('event-notice');
    if (!notice) {
      notice = document.createElement('div'); notice.id = 'event-notice'; notice.className = 'alert';
      notice.setAttribute('role','status'); document.querySelector('main').prepend(notice);
    }
    notice.textContent = 'New order updates available. Finish or save your current work before refreshing. ';
    const link = document.createElement('a'); link.href = location.href; link.textContent = 'Refresh view'; notice.append(link);
  }
  async function poll() {
    if (document.hidden || !navigator.onLine) return;
    try {
      const response = await fetch(`/api/events?after=${cursor}`, {cache:'no-store'});
      if (response.ok) apply(await response.json());
    } catch { /* Reconnect later. */ }
  }
  function fallback() { source?.close(); if (!timer) { poll(); timer = setInterval(poll,15000); } }
  if ('EventSource' in window) {
    source = new EventSource('/api/events/stream');
    source.onmessage = event => { try { apply(JSON.parse(event.data)); } catch { fallback(); } };
    source.addEventListener('expired', () => { source.close(); location.href='/login'; });
    source.onerror = fallback;
  } else fallback();
  window.addEventListener('pagehide', () => { source?.close(); clearInterval(timer); });
})();

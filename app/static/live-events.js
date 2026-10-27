/* One feed for every role; polling recovers reconnects and suspended tabs. */
(() => {
  if (!window.AppUI) return;
  let revision = null, source, stopped = false, polling = false;
  function apply(data) {
    if (!data.revision) return;
    if (revision !== data.revision) { revision = data.revision; window.AppUI.schedule(); }
  }
  async function poll() {
    if (stopped || polling || document.hidden || !navigator.onLine) return;
    polling = true;
    try {
      const response = await fetch('/api/ui-state', {cache:'no-store'});
      if (response.status === 401 || response.status === 403) { source?.close(); location.assign('/login'); return; }
      if (response.ok) apply(await response.json());
    } catch { /* Retry without losing local work. */ }
    finally { polling = false; }
  }
  function connect() {
    if (document.hidden || stopped || !navigator.onLine || source || !('EventSource' in window)) return;
    source = new EventSource('/api/events/stream');
    source.onmessage = event => { try { apply(JSON.parse(event.data)); } catch { /* Polling recovers. */ } };
    source.addEventListener('expired', () => { source?.close(); source = null; location.assign('/login'); });
    source.onerror = () => { source?.close(); source = null; poll(); };
  }
  setInterval(() => { poll(); connect(); }, 15000);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { source?.close(); source = null; }
    else { poll(); connect(); }
  });
  window.addEventListener('online', () => { poll(); connect(); });
  window.addEventListener('pagehide', () => { stopped = true; source?.close(); source = null; });
  window.addEventListener('pageshow', () => { stopped = false; poll(); connect(); });
  poll(); connect();
})();

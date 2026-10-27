(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const bound = new WeakSet();
  function bind() { document.querySelectorAll('[data-capture-location]').forEach(button => {
    if (bound.has(button)) return;
    bound.add(button);
    button.addEventListener('click', () => {
      const id = button.dataset.captureLocation;
      const output = document.querySelector(`[data-location-result="${id}"]`);
      if (!navigator.geolocation) { output.textContent = 'GPS is unavailable in this browser.'; return; }
      output.textContent = 'Requesting current location…';
      navigator.geolocation.getCurrentPosition(async ({coords}) => {
        try {
          const body = new URLSearchParams({latitude: coords.latitude, longitude: coords.longitude});
          const result = await fetch(`/delivery/tasks/${id}/location`, {method:'POST', headers:{'X-CSRF-Token':csrf}, body});
          const data = await result.json();
          output.textContent = result.ok ? 'Current location saved.' : data.detail;
        } catch { output.textContent = 'Unable to save location. Check your connection.'; }
      }, error => { output.textContent = error.message; }, {enableHighAccuracy:true, timeout:15000, maximumAge:0});
    });
  }); }
  bind();
  document.addEventListener('app:updated', bind);
})();

(() => {
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-fill-onboarding-location]');
    if (!button || button.disabled) return;
    const form = button.form;
    const output = form.querySelector('[data-onboarding-location-status]');
    const latitude = form.elements.latitude, longitude = form.elements.longitude;
    if (!navigator.geolocation) { output.textContent = 'GPS is unavailable. Enter latitude and longitude manually.'; return; }
    if (!window.isSecureContext) { output.textContent = 'Location requires HTTPS. You can enter coordinates manually.'; return; }
    // Preserve the in-progress capture and all entries during background updates.
    latitude.dispatchEvent(new Event('input', {bubbles:true}));
    button.disabled = true;
    output.textContent = 'Getting your current location…';
    navigator.geolocation.getCurrentPosition(({coords}) => {
      if (form.isConnected) {
        latitude.value = coords.latitude.toFixed(6);
        longitude.value = coords.longitude.toFixed(6);
        [latitude, longitude].forEach(input => input.dispatchEvent(new Event('input', {bubbles:true})));
        output.textContent = `Location captured. Accuracy approximately ${Math.round(coords.accuracy)} metres. It will be saved when you create the partner account.`;
      }
      button.disabled = false;
    }, error => {
      const messages = {1:'Location permission denied. Allow location for this site, or enter coordinates manually.',
        2:'Current location is unavailable. Try again or enter coordinates manually.',
        3:'Location request timed out. Try again or enter coordinates manually.'};
      output.textContent = messages[error.code] || 'Unable to capture location. Enter coordinates manually.';
      button.disabled = false;
    }, {enableHighAccuracy:true, timeout:15000, maximumAge:0});
  });
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

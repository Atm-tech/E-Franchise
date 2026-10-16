(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const driverTasks = [...document.querySelectorAll('[data-live-driver-task]')];
  const trackingList = document.getElementById('live-tracking-list');
  const cards = new Map();
  const nativeApp = Boolean(window.Capacitor?.isNativePlatform?.());
  const backgroundGeo = window.AapoortiBackgroundGeolocation;
  const localNotifications = window.AapoortiLocalNotifications;
  let nativeSharingActive = localStorage.getItem('aapoorti-background-location-active') === '1';
  let browserWatchId = null;
  let sharing = false;
  const statuses = {OUT_FOR_DELIVERY: 'On the way to you'};

  async function sendLocation(taskId, latitude, longitude) {
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
    const body = new URLSearchParams({latitude: String(latitude), longitude: String(longitude)}).toString();
    try {
      await fetch(`/delivery/tasks/${taskId}/location`, {
        method: 'POST', headers: {'X-CSRF-Token': csrf, 'Content-Type': 'application/x-www-form-urlencoded'}, body, keepalive: true
      });
    } catch { /* The next location callback will retry. */ }
  }

  function displaySharing(message, started = false) {
    document.querySelectorAll('[data-live-sharing-result]').forEach(element => { element.textContent = message; });
    document.querySelectorAll('[data-start-live-sharing]').forEach(button => { button.hidden = started; });
    document.querySelectorAll('[data-stop-live-sharing]').forEach(button => { button.hidden = !started; });
  }

  function handleLocation(location) {
    if (!location || !Number.isFinite(location.latitude) || !Number.isFinite(location.longitude)) return;
    driverTasks.forEach(card => sendLocation(card.dataset.liveDriverTask, location.latitude, location.longitude));
    displaySharing(nativeApp ? 'Live sharing is active, including while the app is in the background.' : 'Live sharing is active while this browser page is open.', true);
  }

  async function stopSharing() {
    if (nativeSharingActive && backgroundGeo?.stop) {
      try { await backgroundGeo.stop(); } catch { /* Native tracking may already have stopped. */ }
      nativeSharingActive = false;
      localStorage.removeItem('aapoorti-background-location-active');
    }
    if (browserWatchId !== null && navigator.geolocation) {
      navigator.geolocation.clearWatch(browserWatchId);
      browserWatchId = null;
    }
    sharing = false;
    displaySharing('Location sharing is stopped.', false);
  }

  async function startSharing() {
    if (sharing) return;
    if (!driverTasks.length) return;
    if (nativeApp && backgroundGeo?.addWatcher) {
      try {
        if (localNotifications?.requestPermissions) {
          const permission = await localNotifications.requestPermissions();
          if (permission.display === 'denied') throw new Error('Allow notifications so the tracking indicator stays visible.');
        }
        await backgroundGeo.start({
          backgroundMessage: 'Location is being shared for your active delivery. Open Aapoorti Partner to stop.',
          backgroundTitle: 'Aapoorti delivery tracking', requestPermissions: true, stale: false, distanceFilter: 25
        }, (location, error) => {
          if (error) { displaySharing(error.message || 'Location permission is needed for tracking.'); return; }
          handleLocation(location);
        });
        localStorage.setItem('aapoorti-background-location-active', '1');
        nativeSharingActive = true;
        sharing = true;
        displaySharing('Live sharing is active. The phone will show a tracking notification in the background.', true);
      } catch (error) {
        displaySharing(error?.message || 'Could not start background location sharing. Check location and notification permissions.');
      }
      return;
    }
    if (!navigator.geolocation) { displaySharing('Location is unavailable in this browser.'); return; }
    displaySharing('Requesting location permission…');
    browserWatchId = navigator.geolocation.watchPosition(
      position => handleLocation(position.coords),
      error => displaySharing(error.message || 'Allow location access to share this delivery.'),
      {enableHighAccuracy: true, timeout: 20000, maximumAge: 10000}
    );
    sharing = true;
  }

  document.querySelectorAll('[data-start-live-sharing]').forEach(button => button.addEventListener('click', startSharing));
  document.querySelectorAll('[data-stop-live-sharing]').forEach(button => button.addEventListener('click', stopSharing));
  document.querySelector('form[action="/logout"]')?.addEventListener('submit', stopSharing);

  if (driverTasks.length && nativeSharingActive) {
    sharing = true;
    displaySharing('Background sharing is active for this delivery. The phone shows a tracking notification.', true);
  }

  async function clearOldWatcherWhenNotOnTrip() {
    if (!document.querySelector('[data-delivery-dashboard]') || driverTasks.length || !nativeSharingActive || !backgroundGeo?.stop) return;
    try { await backgroundGeo.stop(); } catch { /* Native service may already have stopped. */ }
    localStorage.removeItem('aapoorti-background-location-active');
    nativeSharingActive = false;
  }
  clearOldWatcherWhenNotOnTrip();

  function formatEta(seconds) {
    const eta = new Date(Date.now() + seconds * 1000);
    return `About ${eta.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}`;
  }

  function createTrackingCard(task) {
    const card = document.createElement('article');
    card.className = 'live-tracking-card';
    const heading = document.createElement('div'); heading.className = 'tracking-card-heading';
    const title = document.createElement('b'); title.textContent = `${task.dispatch_number || 'Dispatch'} · Task #${task.task_id}`;
    const status = document.createElement('span'); status.className = 'status out_for_delivery'; status.textContent = statuses[task.status] || task.status;
    heading.append(title, status);
    const meta = document.createElement('p'); meta.className = 'tracking-fresh';
    const map = document.createElement('div'); map.className = 'live-tracking-map'; map.setAttribute('role', 'img'); map.setAttribute('aria-label', 'Live delivery route map');
    const routeInfo = document.createElement('p'); routeInfo.className = 'tracking-route-info'; routeInfo.textContent = 'Waiting for location and route…';
    const link = document.createElement('a'); link.target = '_blank'; link.rel = 'noopener'; link.textContent = 'Open directions';
    card.append(heading, meta, map, routeInfo, link);
    const leafletMap = window.L?.map(map, {scrollWheelZoom: false}).setView([22.9734, 78.6569], 5);
    if (leafletMap) {
      window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
      }).addTo(leafletMap);
    } else {
      map.textContent = 'Map library unavailable.';
    }
    return {element: card, meta, routeInfo, link, map: leafletMap, driverMarker: null, destinationMarker: null, routeLine: null, lastRouteKey: '', lastRouteAt: 0};
  }

  async function updateRoute(task, view, driverLatLng, destinationLatLng) {
    if (!view.map || !destinationLatLng) { view.routeInfo.textContent = 'Franchise map coordinates are not set.'; return; }
    const currentKey = `${task.task_id}:${driverLatLng[0].toFixed(3)},${driverLatLng[1].toFixed(3)}:${destinationLatLng[0].toFixed(4)},${destinationLatLng[1].toFixed(4)}`;
    if (view.lastRouteKey === currentKey && Date.now() - view.lastRouteAt < 120000) return;
    view.lastRouteKey = currentKey; view.lastRouteAt = Date.now();
    view.routeInfo.textContent = 'Updating driving route and ETA…';
    try {
      const response = await fetch(`/franchise/tracking/${task.task_id}/route`, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Driving route is not available.');
      if (view.routeLine) view.map.removeLayer(view.routeLine);
      view.routeLine = window.L.polyline(data.coordinates, {color: '#08764d', weight: 5, opacity: 0.82}).addTo(view.map);
      view.map.fitBounds(view.routeLine.getBounds().pad(0.14), {maxZoom: 15});
      const minutes = Math.max(1, Math.ceil(data.duration_seconds / 60));
      view.routeInfo.textContent = `ETA ${formatEta(data.duration_seconds)} · ${minutes} min · ${data.distance_km} km · route estimate without live traffic.`;
    } catch (error) {
      view.routeInfo.textContent = error.message;
      if (view.routeLine) { view.map.removeLayer(view.routeLine); view.routeLine = null; }
      view.routeLine = window.L.polyline([driverLatLng, destinationLatLng], {color: '#8a9a90', weight: 3, dashArray: '7 8'}).addTo(view.map);
      view.map.fitBounds(view.routeLine.getBounds().pad(0.2), {maxZoom: 15});
    }
  }

  function renderTasks(tasks) {
    if (!trackingList) return;
    const active = tasks.filter(task => task.status === 'OUT_FOR_DELIVERY');
    const ids = new Set(active.map(task => String(task.task_id)));
    for (const [id, view] of cards) {
      if (!ids.has(id)) { view.map?.remove(); view.element.remove(); cards.delete(id); }
    }
    if (!active.length) {
      if (!trackingList.querySelector('.tracking-empty')) {
        trackingList.replaceChildren(); const empty = document.createElement('p'); empty.className = 'tracking-empty';
        empty.textContent = tasks.length ? 'No driver is currently on the way.' : 'No active delivery trips right now.'; trackingList.append(empty);
      }
      return;
    }
    trackingList.querySelector('.tracking-empty')?.remove();
    active.forEach(task => {
      const id = String(task.task_id);
      let view = cards.get(id);
      if (!view) { view = createTrackingCard(task); cards.set(id, view); trackingList.append(view.element); }
      const hasPosition = Number.isFinite(task.latitude) && Number.isFinite(task.longitude) && Boolean(task.location_at);
      if (!hasPosition) { view.meta.textContent = 'Waiting for the driver’s first GPS update.'; view.routeInfo.textContent = 'ETA will appear after the first location and route are available.'; return; }
      const latLng = [Number(task.latitude), Number(task.longitude)];
      const captured = new Date(task.location_at.replace(' ', 'T') + '+05:30');
      const ageSeconds = Math.max(0, (Date.now() - captured.getTime()) / 1000);
      const fresh = ageSeconds <= 90;
      view.meta.className = fresh ? 'tracking-fresh' : 'tracking-stale';
      view.meta.textContent = `${fresh ? 'Live location' : 'Last known location'} · ${captured.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`;
      if (view.map) {
        if (!view.driverMarker) view.driverMarker = window.L.marker(latLng).addTo(view.map).bindPopup('Delivery partner');
        else view.driverMarker.setLatLng(latLng);
      }
      const dest = [Number(task.destination_latitude), Number(task.destination_longitude)];
      const hasDestination = dest.every(Number.isFinite) && (dest[0] !== 0 || dest[1] !== 0);
      if (view.map && hasDestination) {
        if (!view.destinationMarker) view.destinationMarker = window.L.marker(dest).addTo(view.map).bindPopup('Franchise destination');
        else view.destinationMarker.setLatLng(dest);
        view.link.href = `https://www.google.com/maps/dir/?api=1&origin=${encodeURIComponent(latLng.join(','))}&destination=${encodeURIComponent(dest.join(','))}&travelmode=driving`;
        if (!fresh) { view.routeInfo.textContent = 'Location is stale; ETA is paused until a fresh driver update arrives.'; return; }
        updateRoute(task, view, latLng, dest);
      } else {
        view.link.removeAttribute('href');
        view.routeInfo.textContent = 'Ask the administrator to set the franchise map coordinates.';
      }
    });
  }

  async function refreshPartnerTracking() {
    if (!trackingList || document.hidden || !document.getElementById('orders-view')?.classList.contains('active')) return;
    try {
      const response = await fetch('/franchise/tracking', {cache: 'no-store'});
      if (response.ok) renderTasks((await response.json()).tasks || []);
    } catch { /* Preserve the most recent tracking display. */ }
  }

  document.addEventListener('appviewchange', event => {
    if (event.detail?.id !== 'orders-view') return;
    window.setTimeout(() => cards.forEach(view => view.map?.invalidateSize(true)), 120);
    refreshPartnerTracking();
  });

  if (trackingList) {
    if (document.getElementById('orders-view')?.classList.contains('active')) refreshPartnerTracking();
    const timer = setInterval(refreshPartnerTracking, 10000);
    window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
    document.addEventListener('visibilitychange', refreshPartnerTracking);
  }
})();

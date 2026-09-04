(() => {
  const loader = document.getElementById('app-loader');
  const loaderStartedAt = performance.now();
  const minimumLoaderTime = 2200;
  let hideScheduled = false;
  const loaderStatus = document.getElementById('loader-status-text');
  const loaderStages = [
    [650, 'Preparing store and orders'],
    [1350, 'Connecting fulfilment route'],
    [1850, 'Workspace ready']
  ];
  loaderStages.forEach(([delay, message]) => window.setTimeout(() => {
    if (loaderStatus && !loader?.classList.contains('loader-hidden')) loaderStatus.textContent = message;
    if (delay === 1850) loader?.classList.add('loader-complete');
  }, delay));
  const hideLoader = () => {
    if (!loader || loader.classList.contains('loader-hidden') || hideScheduled) return;
    hideScheduled = true;
    const remaining = Math.max(0, minimumLoaderTime - (performance.now() - loaderStartedAt));
    window.setTimeout(() => {
      loader.classList.add('loader-hidden');
      window.setTimeout(() => { loader.hidden = true; }, 550);
    }, remaining);
  };
  const showLoader = () => {
    if (!loader) return;
    hideScheduled = false;
    loader.classList.remove('loader-complete');
    loader.hidden = false;
    requestAnimationFrame(() => loader.classList.remove('loader-hidden'));
  };
  if (document.readyState === 'complete') hideLoader();
  else window.addEventListener('load', hideLoader, { once: true });
  window.setTimeout(hideLoader, 7000);
  document.addEventListener('submit', event => {
    if (!event.defaultPrevented) showLoader();
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
    const url = new URL(link.href, location.href);
    if (url.origin === location.origin && (url.pathname !== location.pathname || url.search !== location.search)) showLoader();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) { hideScheduled = false; hideLoader(); } });
  if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js'));
  const installButton = document.querySelector('.install-app');
  let installPrompt;
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault(); installPrompt = event;
    if (installButton) installButton.hidden = false;
  });
  installButton?.addEventListener('click', async () => {
    if (!installPrompt) return;
    await installPrompt.prompt(); installPrompt = null; installButton.hidden = true;
  });
  window.addEventListener('appinstalled', () => { if (installButton) installButton.hidden = true; });
  const offlinePill = document.querySelector('.offline-pill');
  const updateNetworkState = () => {
    if (offlinePill) offlinePill.hidden = navigator.onLine;
    document.documentElement.classList.toggle('is-offline', !navigator.onLine);
  };
  window.addEventListener('online', updateNetworkState);
  window.addEventListener('offline', updateNetworkState);
  updateNetworkState();
})();

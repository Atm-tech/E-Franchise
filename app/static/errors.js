/* Shared user-facing errors for every role, including guest pages. */
(() => {
  function notice(message) {
    let node = document.getElementById('ui-error-popup');
    if (!node) {
      node = document.createElement('dialog'); node.id = 'ui-error-popup';
      node.setAttribute('aria-label', 'Action needed');
      node.style.cssText = 'position:fixed;inset:0;margin:auto;width:min(90vw,460px);max-height:80vh;overflow:auto;padding:24px;border:2px solid #b42318;border-radius:18px;box-shadow:0 20px 80px #0005;background:white;color:#182b26;z-index:2147483647';
      const title = document.createElement('h2'); title.textContent = 'Action needed';
      const text = document.createElement('p'); text.setAttribute('role', 'alert');
      const close = document.createElement('button'); close.type = 'button'; close.textContent = 'OK';
      close.addEventListener('click', () => node.close());
      node.append(title, text, close); document.body.append(node);
    }
    node.querySelector('p').textContent = message;
    if (!node.open) node.showModal();
  }

  let toastTimer;
  function success(message) {
    if (!message) return;
    let node = document.getElementById('ui-success-toast');
    if (!node) {
      node = document.createElement('div'); node.id = 'ui-success-toast';
      node.setAttribute('role', 'status'); node.setAttribute('aria-live', 'polite');
      node.style.cssText = 'position:fixed;top:calc(env(safe-area-inset-top,0px) + 20px);left:50%;transform:translateX(-50%);width:min(90vw,460px);box-sizing:border-box;padding:16px 20px;display:flex;align-items:center;gap:16px;border-radius:14px;background:#0b4f3c;color:white;box-shadow:0 8px 32px #0004;z-index:2147483646';
      const text = document.createElement('span'); text.style.flex = '1';
      const close = document.createElement('button'); close.type = 'button';
      close.textContent = '?'; close.setAttribute('aria-label', 'Dismiss notification');
      close.style.cssText = 'background:transparent;color:white;border:0;padding:4px;font-size:24px;min-width:32px';
      close.addEventListener('click', () => { node.hidden = true; node.style.display = 'none'; clearTimeout(toastTimer); });
      node.append(text, close); document.body.append(node);
    }
    node.querySelector('span').textContent = '? ' + message;
    node.hidden = false; node.style.display = 'flex';
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; node.style.display = 'none'; }, 5000);
  }
  window.AppNotify = {success, error: notice};
  const initialSuccess = document.querySelector('main .order-success');
  if (initialSuccess) success(initialSuccess.querySelector('b')?.textContent || initialSuccess.textContent.trim());
  else if (new URL(location.href).searchParams.get('message')) success(new URL(location.href).searchParams.get('message'));
  document.addEventListener('app:submitted', event => {
    if (!event.detail.ok) return;
    const url = event.detail.url;
    success(url.searchParams.get('message') || 'Saved successfully');
  });
  window.AppErrors = {show: notice};
  window.alert = message => notice(String(message));
  const seen = new WeakMap();
  function scan() {
    document.querySelectorAll('.order-error,.alert.error,.alert.danger,[data-error-message]').forEach(node => {
      const text = node.textContent.trim();
      if (text && !node.hidden && seen.get(node) !== text) { seen.set(node, text); notice(text); }
    });
  }
  if (location.pathname === '/login') document.querySelector('main .alert')?.setAttribute('data-error-message', '');
  scan();
  new MutationObserver(scan).observe(document.querySelector('main') || document.body, {subtree:true,childList:true,characterData:true});
  document.addEventListener('invalid', event => {
    const field = event.target;
    const label = field.labels?.[0]?.childNodes[0]?.textContent?.trim() || field.name?.replaceAll('_',' ') || 'Field';
    notice(label + ': ' + field.validationMessage);
  }, true);
  const fetchOriginal = window.fetch.bind(window);
  const background = url => /\/api\/(ui-state|events\/stream)$/.test(url.pathname) || /\/location$/.test(url.pathname) || /\/live(-state)?$/.test(url.pathname);
  window.fetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const quiet = url.origin !== location.origin || background(url) || init.headers?.['X-UI-Update'] === '1';
    try {
      const response = await fetchOriginal(input, init);
      if (!quiet && !response.ok) {
        let message = response.status === 401 ? 'Your session expired. Sign in again.' : response.status === 403 ? 'You do not have permission for this action. Refresh your session and try again.' : 'The request failed. Please try again.';
        try {
          const data = await response.clone().json();
          if (typeof data.detail === 'string') message = data.detail;
          else if (Array.isArray(data.detail)) message = data.detail.map(item => String(item.loc?.at(-1) || 'Field').replaceAll('_',' ') + ': ' + item.msg).join('\n');
          else if (typeof data.error === 'string') message = data.error;
        } catch { /* Keep friendly fallback for HTML/server failures. */ }
        notice(message);
      }
      return response;
    } catch (error) {
      if (!quiet && error.name !== 'AbortError') notice('Connection lost. Your action may not have completed. Check the latest state before retrying.');
      throw error;
    }
  };
  window.addEventListener('error', event => {
    if (event instanceof ErrorEvent && event.error) notice('Something went wrong on this screen. Please try again or refresh the page.');
  });
  window.addEventListener('unhandledrejection', event => {
    if (event.reason?.name !== 'AbortError') notice('The action could not finish. Please check your connection and try again.');
  });
})();

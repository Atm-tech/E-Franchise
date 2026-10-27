/* Shared, authorized server-rendered updates. Never execute fetched scripts. */
(() => {
  if (!document.querySelector('form[action="/logout"]') || !window.morphdom) return;
  const originalFetch = window.fetch.bind(window);
  const dirtyForms = new WeakSet(), hooks = new Set();
  let running = false, pending = false, stopped = false, mutationCount = 0, mutationVersion = 0, scheduled;
  const localState = '.app-view,.admin-panel,.op-panel,[data-admin-tab],[data-op-tab],[data-nav-view],[data-guard-filter],[data-delivery-filter],.function-tray,.partner-hero,.quick-strip';
  const preserve = '[data-ui-preserve],#cart-drawer,#wallet-topup,#reorder-confirm,#customer-directory,#customer-suggestions,#live-tracking-list,.offline-drafts,.image-credits';
  function key(node) { return node.nodeType === 1 ? node.id || node.dataset.uiKey : undefined; }
  function identify(root) {
    root.querySelectorAll('[data-outlet-card]').forEach(node => { node.dataset.uiKey = 'outlet:' + node.dataset.outletCard; });
    root.querySelectorAll('[data-order-id]').forEach(node => { node.dataset.uiKey = 'order:' + node.dataset.orderId; });
    root.querySelectorAll('.catalog-card').forEach(node => {
      const id = node.querySelector('.add-product')?.dataset.id;
      if (id) node.dataset.uiKey = 'product:' + id;
    });
    const counts = new Map();
    root.querySelectorAll('form').forEach(form => {
      const entity = [...form.querySelectorAll('input[type=hidden]')]
        .filter(input => /^(entity_type|entity_id|order_id|bill_id|task_id|product_id|franchisee_id|status)$/.test(input.name))
        .map(input => input.name + '=' + input.value).join('&');
      const base = 'form:' + form.getAttribute('action') + ':' + entity;
      const index = counts.get(base) || 0; counts.set(base, index + 1);
      form.dataset.uiKey = base + ':' + index;
    });
  }
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
  const initialError = document.querySelector('main .order-error,main .alert.error');
  if (initialError) notice(initialError.textContent.trim());
  function apply(doc, {submitted = null, reset = false} = {}) {
    const current = document.querySelector('main'), next = doc.querySelector('main');
    if (!current || !next || !doc.querySelector('form[action="/logout"]') ||
        doc.body.dataset.userId !== document.body.dataset.userId) throw new Error('Session changed');
    identify(document); identify(doc);
    if (reset && submitted) dirtyForms.delete(submitted);
    const focus = document.activeElement;
    const selection = focus?.selectionStart == null ? null : [focus.selectionStart, focus.selectionEnd];
    const scroll = [window.scrollX, window.scrollY];
    const options = {
      getNodeKey: key,
      onBeforeNodeAdded: node => node.nodeName === 'SCRIPT' && node.type !== 'application/json' ? false : node,
      onBeforeNodeDiscarded: node => {
        if (node.nodeType !== 1) return true;
        if (node.matches(preserve) || node.matches('[data-ui-flash],script:not([type="application/json"])')) return false;
        return ![...node.querySelectorAll('form'), ...(node.matches('form') ? [node] : [])]
          .some(form => dirtyForms.has(form) && form !== submitted);
      },
      onBeforeElUpdated: (from, to) => {
        if (from.matches('script:not([type="application/json"])') || from.matches(preserve)) return false;
        if (from.matches('form') && dirtyForms.has(from) && !(from === submitted && reset)) return false;
        if (from.matches(localState)) {
          for (const name of ['active', 'view-hidden', 'show-live-commission']) to.classList.toggle(name, from.classList.contains(name));
        }
        if (from.matches('details,dialog')) to.toggleAttribute('open', from.open);
        if (from.matches('input,textarea,select') && !(reset && from.form === submitted)) {
          if (from.type === 'file') return false;
          if (from.type === 'checkbox' || from.type === 'radio') to.checked = from.checked;
          else if (from.type !== 'hidden' || from.name === 'request_key') to.value = from.value;
          if (from.matches('select')) [...to.options].forEach(option => { option.selected = [...from.selectedOptions].some(old => old.value === option.value); });
        }
        // Equality ignores live input values, including those inside equal wrappers.
        return true;
      }
    };
    window.morphdom(current, next, options);
    const header = document.querySelector('.topbar'), nextHeader = doc.querySelector('.topbar');
    if (header && nextHeader) window.morphdom(header, nextHeader, options);
    hooks.forEach(hook => hook(doc, {submitted, reset}));
    document.dispatchEvent(new CustomEvent('app:updated', {detail:{submitted, reset, document:doc}}));
    if (focus?.isConnected) {
      focus.focus({preventScroll:true});
      if (selection) { try { focus.setSelectionRange(...selection); } catch { /* Number/select fields. */ } }
    }
    window.scrollTo(...scroll);
    document.getElementById('ui-sync-status')?.remove();
  }
  function parse(html) { return new DOMParser().parseFromString(html, 'text/html'); }
  async function refresh() {
    if (stopped || document.hidden || !navigator.onLine) { pending = true; return; }
    if (running || mutationCount) { pending = true; return; }
    running = true; pending = false;
    const url = location.pathname + location.search, version = mutationVersion;
    try {
      const response = await originalFetch(url, {cache:'no-store', headers:{'X-UI-Update':'1'}});
      if (url !== location.pathname + location.search || mutationCount || version !== mutationVersion) { pending = true; return; }
      if (!response.ok) throw new Error('Unable to sync');
      if (new URL(response.url).pathname !== location.pathname) { location.assign(response.url); return; }
      const doc = parse(await response.text());
      if (mutationCount || version !== mutationVersion) { pending = true; return; }
      apply(doc);
    } catch {
      pending = true; notice('Updates will reconnect automatically. Your current work is kept.');
    } finally { running = false; }
  }
  function schedule() { pending = true; clearTimeout(scheduled); scheduled = setTimeout(refresh, 150); }
  window.AppUI = {refresh, schedule, register: hook => hooks.add(hook), apply, isDirty: form => dirtyForms.has(form)};
  document.addEventListener('input', event => { if (event.target.form) dirtyForms.add(event.target.form); });
  document.addEventListener('change', event => { if (event.target.form) dirtyForms.add(event.target.form); });
  document.addEventListener('reset', event => { dirtyForms.delete(event.target); schedule(); });
  // JSON actions share invalidation without changing their existing response contracts.
  window.fetch = async (input, init = {}) => {
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (!['GET','HEAD','OPTIONS'].includes(method) && url.origin === location.origin) mutationVersion++;
    const response = await originalFetch(input, init);
    if (!['GET','HEAD','OPTIONS'].includes(method) && url.origin === location.origin && response.ok &&
        !/\/location$/.test(url.pathname)) schedule();
    return response;
  };
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (event.defaultPrevented || form.method.toLowerCase() !== 'post' || form.target) return;
    const url = new URL(form.action, location.href);
    if (url.origin !== location.origin || ['/login','/logout'].includes(url.pathname) || form.hasAttribute('data-full-navigation')) return;
    event.preventDefault();
    if (!navigator.onLine) { notice('Offline. Your entries are kept; reconnect before saving.'); return; }
    if (form.dataset.uiSubmitting) return;
    form.dataset.uiSubmitting = 'true'; mutationCount++; mutationVersion++;
    const submitter = event.submitter, data = new FormData(form);
    if (submitter?.name) data.append(submitter.name, submitter.value);
    const wasDisabled = submitter?.disabled;
    if (submitter) submitter.disabled = true;
    let applied = false, succeeded = false;
    try {
      const response = await originalFetch(url, {method:'POST',body:data,headers:{'X-UI-Update':'1'}});
      if (!response.ok) {
        let message = 'Could not save. Your entries are kept; please try again.';
        try { const error = await response.json(); if (typeof error.detail === 'string') message = error.detail; else if (Array.isArray(error.detail)) message = error.detail.map(item => { const field = String(item.loc?.at(-1) || 'Field').replaceAll('_', ' '); return field + ': ' + item.msg; }).join('\n'); } catch { /* Non-JSON error. */ }
        throw new Error(message);
      }
      const destination = new URL(response.url);
      if (destination.pathname !== location.pathname) { location.assign(response.url); return; }
      if (!response.headers.get('content-type')?.includes('text/html')) { schedule(); return; }
      const reset = !destination.searchParams.has('error');
      succeeded = reset;
      document.querySelectorAll('[data-ui-flash]').forEach(node => node.remove());
      const doc = parse(await response.text());
      doc.querySelectorAll('main .order-success,main .order-error,main > .alert').forEach(node => node.setAttribute('data-ui-flash',''));
      apply(doc, {submitted:form, reset}); applied = true;
      if (!reset) notice(destination.searchParams.get('error') || doc.querySelector('.order-error')?.textContent || 'Could not save. Please check your entries.');
      document.dispatchEvent(new CustomEvent('app:submitted', {detail:{form, url:destination, ok:reset}}));
      schedule();
    } catch (error) {
      // Never retry a write whose network outcome is uncertain.
      notice(error.message || 'Connection lost. Your entries are kept. Check the latest state before retrying.');
      pending = true;
    } finally {
      delete form.dataset.uiSubmitting; mutationCount--;
      if ((!applied || !succeeded) && submitter?.isConnected) submitter.disabled = Boolean(wasDisabled);
      const loader = document.getElementById('app-loader');
      if (loader) { loader.classList.add('loader-hidden'); loader.hidden = true; }
    }
  });
  const resume = () => { if (!document.hidden) schedule(); };
  document.addEventListener('visibilitychange', resume);
  window.addEventListener('online', resume);
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; schedule(); } });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(scheduled); });
  setInterval(() => { if (pending) refresh(); }, 5000);
})();

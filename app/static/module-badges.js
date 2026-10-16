/* Pending work, scoped to the role's existing server-authorized page data. */
(() => {
  const all = selector => [...document.querySelectorAll(selector)];
  function badge(button, count) {
    if (!button) return;
    let node = button.querySelector('.module-pending-badge,.nav-pending-badge,.guard-tab-count');
    if (!node && !count) return;
    if (!node) {
      node = document.createElement('b');
      node.className = 'module-pending-badge';
      button.appendChild(node);
    }
    if (node.textContent !== String(count)) node.textContent = String(count);
    if (node.hidden !== !count) node.hidden = !count;
    const label = count + ' pending';
    if (node.getAttribute('aria-label') !== label) node.setAttribute('aria-label', label);
  }
  function update() {
    all('[data-op-tab]').forEach(button => {
      const cards = all('#op-' + button.dataset.opTab + ' [data-outlet-franchise]');
      badge(button, cards.filter(card => !card.hidden && card.dataset.pendingWork === 'true').length);
    });
    all('[data-guard-filter]').forEach(button => {
      const key = button.dataset.guardFilter;
      badge(button, all('[data-guard-kind]').filter(card => key === 'all' || card.dataset.guardKind === key || (key === 'alerts' && card.dataset.guardAlert === 'true')).length);
    });
    const deliveryPending = all('[data-delivery-state="active"]').length;
    all('[data-delivery-filter]').forEach(button => badge(button, button.dataset.deliveryFilter === 'completed' ? 0 : deliveryPending));
    const orders = all('#filtered-orders .order-row-detailed');
    if (orders.length || document.getElementById('filtered-orders')) {
      badge(document.querySelector('[data-nav-view="orders-view"]'), orders.filter(card => ['DELIVERED','PICKED_BY_FRANCHISEE'].includes(card.dataset.orderStatus)).length);
      const readyDockets = all('#dispatch-source [data-dispatch-kind="DOCKET"]').filter(card => card.dataset.dispatchStatus === 'WEIGHT_CHECK_PASSED' && card.dataset.dispatchMode === 'FRANCHISE_PICKUP').length;
      const pendingBills = orders.filter(card => ['DELIVERED','PICKED_BY_FRANCHISEE'].includes(card.dataset.orderStatus) && card.dataset.billNumber).length;
      badge(document.querySelector('[data-nav-view="outlet-processing-view"]'), readyDockets);
      [['dockets',readyDockets],['bills',pendingBills]].forEach(([key,count]) => {
        const button = document.querySelector('[data-pending-summary="'+key+'"]');
        if (!button) return;
        const number = button.querySelector('b');
        if (number.textContent !== String(count)) number.textContent = String(count);
        if (button.hidden !== !count) button.hidden = !count;
      });
    }
    all('[data-pending-count]').forEach(button => badge(button, Number(button.dataset.pendingCount) || 0));
    all('[data-admin-tab]').forEach(button => {
      const source = document.querySelector('[data-admin-tab="' + button.dataset.adminTab + '"][data-pending-count]');
      if (source) badge(button, Number(source.dataset.pendingCount) || 0);
    });
  }
  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; update(); });
  });
  update();
  observer.observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden','data-order-status','data-dispatch-status','data-delivery-state','data-pending-count']});
})();

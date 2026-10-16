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
      const unbilled = orders.filter(card => !card.dataset.billNumber && !['CUSTOMER_DELIVERED','DELIVERED','PICKED_BY_FRANCHISEE','CANCELLED'].includes(card.dataset.orderStatus)).length;
      const dispatches = all('#dispatch-source [data-dispatch-status]').filter(card => !['DELIVERED','PICKED_BY_FRANCHISEE'].includes(card.dataset.dispatchStatus) && card.dataset.dispatchTask !== 'DELIVERED').length;
      badge(document.querySelector('[data-nav-view="outlet-processing-view"]'), unbilled + dispatches);
      const readyDockets = all('#dispatch-source [data-dispatch-kind="DOCKET"]').filter(card => card.dataset.dispatchStatus === 'WEIGHT_CHECK_PASSED').length;
      const pendingBills = orders.filter(card => card.dataset.billNumber && !['DELIVERED','PICKED_BY_FRANCHISEE','CUSTOMER_DELIVERED','CANCELLED'].includes(card.dataset.orderStatus)).length;
      [['[data-franchise-ready-dockets]',readyDockets],['[data-franchise-pending-bills]',pendingBills]].forEach(([selector,count]) => all(selector).forEach(node => {if (node.textContent !== String(count)) node.textContent = String(count);}));

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

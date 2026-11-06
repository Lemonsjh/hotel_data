(() => {
  const root = document.getElementById('collection-status');
  if (!root) return;
  let timer, polling = false;

  // 只修改有变化的节点；不重建整页，也不覆盖用户展开的子采集详情。
  function sync(current, fresh) {
    if (current.nodeType !== fresh.nodeType || current.nodeName !== fresh.nodeName) {
      current.replaceWith(fresh.cloneNode(true));
      return;
    }
    if (current.nodeType === Node.TEXT_NODE) {
      if (current.data !== fresh.data) current.data = fresh.data;
      return;
    }
    if (current.nodeType !== Node.ELEMENT_NODE) return;
    const keepOpen = current.tagName === 'DETAILS';
    for (const attribute of [...current.attributes]) {
      if (keepOpen && attribute.name === 'open') continue;
      if (!fresh.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of fresh.attributes) {
      if (keepOpen && attribute.name === 'open') continue;
      if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
    }
    for (let i = 0; i < fresh.childNodes.length; i++) {
      if (current.childNodes[i]) sync(current.childNodes[i], fresh.childNodes[i]);
      else current.appendChild(fresh.childNodes[i].cloneNode(true));
    }
    while (current.childNodes.length > fresh.childNodes.length) current.lastChild.remove();
  }

  function schedule(delay) {
    clearTimeout(timer);
    if (delay > 0) timer = setTimeout(poll, delay);
  }

  async function poll() {
    if (polling) return;
    if (document.hidden) { schedule(15000); return; }
    polling = true;
    let delay = 5000;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/?partial=1', {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error('status unavailable');
      const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
      const fresh = documentCopy.getElementById('collection-status');
      if (!fresh) throw new Error('status missing');
      const x = window.scrollX, y = window.scrollY;
      sync(root, fresh);
      if (window.scrollX !== x || window.scrollY !== y) window.scrollTo(x, y);
      delay = Number(root.dataset.refreshMs) || 0;
    } catch (_) {
      // 连接暂时中断时保留当前页面，稍后重试，不触发整页刷新。
    } finally {
      clearTimeout(timeout);
      polling = false;
      schedule(delay);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { clearTimeout(timer); poll(); }
  });
  schedule(Number(root.dataset.refreshMs) || 0);
})();

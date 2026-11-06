(() => {
  const card = document.getElementById('program-update');
  if (!card) return;
  // An already running panel may still render its cached older template.
  card.classList.remove('panel');
  card.querySelector('.program-update-heading')?.remove();
  document.getElementById('update-checked')?.remove();
  const check = document.getElementById('update-check');
  const apply = document.getElementById('update-apply');
  const message = document.getElementById('update-message');
  let latest = '', active = false, failures = 0;
  function render(data) {
    if (data.token) card.dataset.token = data.token;
    latest = data.latest;
    active = data.busy;
    card.dataset.state = data.status;
    document.getElementById('update-current').textContent = data.current;
    document.getElementById('update-latest').textContent = data.latest;
    const showTarget = data.available || data.status === 'updating';
    const latestRow = document.getElementById('update-latest-row') || document.getElementById('update-latest').parentElement;
    latestRow.hidden = !showTarget;
    message.textContent = data.status === 'error' ? '操作未完成，请重试。' : data.busy ? '处理中…' : '';
    message.title = data.message || '';
    message.hidden = !message.textContent;
    check.disabled = data.busy;
    apply.hidden = !showTarget;
    apply.disabled = data.busy || !data.available;
  }
  async function request(body) {
    const response = await fetch('/api/program-update', body ? {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Update-Token': card.dataset.token},
      body: JSON.stringify(body), cache: 'no-store'
    } : {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '操作失败，请稍后重试。');
    return data;
  }
  async function poll() {
    try { render(await request()); failures = 0; }
    catch (error) {
      failures++;
      message.hidden = false;
      message.textContent = active ? '重启中，正在重连…' : '连接失败，请重试。';
      message.title = error.message;
      if (failures >= 90) { message.textContent = '连接未恢复，请刷新。'; return; }
    }
    setTimeout(poll, active ? 2000 : 5000);
  }
  async function submit(action) {
    if (action === 'apply' && !window.confirm(`确认更新到 ${latest} 并重启程序？`)) return;
    check.disabled = apply.disabled = true;
    try { render(await request({action, target: latest, confirmed: action === 'apply'})); }
    catch (error) { message.textContent = '操作未完成，请重试。'; message.title = error.message; message.hidden = false; check.disabled = false; }
  }
  check.addEventListener('click', () => submit('check'));
  apply.addEventListener('click', () => submit('apply'));
  poll();
})();

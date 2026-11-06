const findHuiyunButton = document.querySelector('[data-find-huiyun-client]');
if (findHuiyunButton) {
  findHuiyunButton.addEventListener('click', async () => {
    const input = document.getElementById('huiyun-client-path');
    const status = document.getElementById('huiyun-client-result');
    findHuiyunButton.disabled = true;
    findHuiyunButton.textContent = '查找中…';
    status.textContent = '';
    try {
      const response = await fetch('/api/huiyun/client-path', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({client_path: input.value}),
      });
      const result = await response.json();
      if (response.ok && result.ok && result.path) {
        input.value = result.path;
        input.dispatchEvent(new Event('input', {bubbles: true}));
      }
      status.textContent = result.message || '查找失败，请重试';
    } catch (_) {
      status.textContent = '查找失败，请检查配置面板连接后重试';
    } finally {
      findHuiyunButton.disabled = false;
      findHuiyunButton.textContent = '自动查找';
    }
  });
}

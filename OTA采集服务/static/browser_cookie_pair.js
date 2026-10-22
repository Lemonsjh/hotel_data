"use strict";
const notice = document.getElementById("message");
async function localAction(action) {
  const response = await fetch("/api/browser-cookie/" + action, {
    method: "POST", cache: "no-store", credentials: "omit",
    headers: {"Content-Type": "application/json",
      "X-Bridge-CSRF": document.querySelector('meta[name="bridge-csrf"]').content},
    body: "{}"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "本机服务暂不可用");
  return data;
}
document.getElementById("create-code").addEventListener("click", async event => {
  event.target.disabled = true;
  try {
    const data = await localAction("code");
    document.getElementById("pair-code").hidden = false;
    const input = document.getElementById("code");
    input.value = data.code;
    input.focus(); input.select();
    document.getElementById("expiry").textContent = "3 分钟有效；请复制到扩展，不要分享。";
    notice.textContent = "配对码已生成。生成新码会使旧码失效。";
  } catch (error) { notice.textContent = error.message; }
  finally { event.target.disabled = false; }
});
document.getElementById("revoke").addEventListener("click", async () => {
  if (!confirm("撤销扩展授权并取消待确认导入？已保存的 Cookie 不会被清空。")) return;
  try {
    const data = await localAction("revoke");
    notice.textContent = data.message;
    document.getElementById("code").value = "";
    document.getElementById("pair-code").hidden = true;
  } catch (error) { notice.textContent = error.message; }
});

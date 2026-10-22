import {readCurrentProfile} from "./cookies.js";
import {verifyCurrentSession} from "./browser_verify.js";

// A native sidebar docks to the browser edge; toolbar popups cannot set their position.
chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(() => {
  chrome.action.setTitle({title: "请在浏览器设置中允许扩展侧栏，或更新浏览器"}).catch(() => {});
});

function validPort(value) {
  const port = String(value || "8765");
  if (!/^\d{1,5}$/.test(port) || +port < 1 || +port > 65535) throw new Error("请输入有效的本机服务端口。");
  return +port;
}

async function api(action, data = {}, override = null) {
  const config = override || (await chrome.storage.local.get("connection")).connection;
  if (!config) throw new Error("请先在配置面板生成配对码并连接。");
  const headers = {"Content-Type": "application/json", "X-Extension-ID": chrome.runtime.id};
  if (config.token) headers.Authorization = "Bearer " + config.token;
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${validPort(config.port)}/api/browser-cookie/${action}`, {
      method: "POST", credentials: "omit", cache: "no-store", redirect: "error", headers,
      body: JSON.stringify(data), signal: AbortSignal.timeout(10000)
    });
  } catch (_) { throw new Error("无法连接本机服务，请确认已启动新版面板、端口正确，并允许扩展访问本机。"); }
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error("服务响应不兼容，请检查本机面板版本。"); }
  if (!response.ok) {
    if (response.status === 403) await chrome.storage.local.remove("connection");
    const error = new Error(result.error || "导入未完成，原配置未修改。");
    error.status = response.status;
    throw error;
  }
  return result;
}

async function dispatch(message) {
  if (message.action === "pair") {
    const code = String(message.code || "").trim().toUpperCase();
    if (!/^[0-9A-F]{32}$/.test(code)) throw new Error("请输入面板生成的 32 位一次性配对码。");
    const port = validPort(message.port);
    const result = await api("pair", {code}, {port});
    await chrome.storage.local.set({connection: {port, token: result.token}});
    await chrome.storage.session.remove("pending");
    return {message: "已连接本机服务。配对码已作废。", paired: true};
  }
  if (message.action === "resume") {
    const {connection} = await chrome.storage.local.get("connection");
    const {pending} = await chrome.storage.session.get("pending");
    if (!connection) return {paired: false};
    if (!pending) return {paired: true, port: connection.port};
    try { return {...await api("status", {id: pending}), paired: true}; }
    catch (error) {
      if ([403, 409].includes(error.status)) {
        await chrome.storage.session.remove("pending");
        return {paired: error.status !== 403, message: error.message};
      }
      throw error;
    }
  }
  if (message.action === "preview") {
    const {pending} = await chrome.storage.session.get("pending");
    if (pending) {
      // Retry only on another explicit click, never as a background reaction to failure.
      let previous;
      try { previous = await api("status", {id: pending}); }
      catch (error) { if (error.status !== 409) throw error; }
      if (previous && previous.state !== "failed") throw new Error("请先完成或取消上次导入。");
      if (previous) await api("cancel", {id: pending});
      await chrome.storage.session.remove("pending");
    }
    // Only this explicit user action reads Cookie values. Never persist them in extension storage.
    const groups = await readCurrentProfile();
    let result = await api("preview", {groups, protocol:"ordinary_browser_v1"});
    await chrome.storage.session.set({pending: result.id});
    if (result.state === "checking") {
      if (result.verification_mode !== "ordinary_browser_v1") throw new Error("请重启本机采集面板，当前进程仍使用旧核对方式。");
      let evidence = await verifyCurrentSession(result.id);
      // A normal protected request can refresh Cookie. Import the final browser values.
      let refreshed = groups;
      if (!evidence.failure_code) {
        try { refreshed = await readCurrentProfile(); }
        catch (_) { evidence = {...evidence, failure_code:"ordinary_cookie_read_failed"}; }
      }
      result = await api("verify", {id:result.id, evidence, groups:refreshed});
    }
    return {...result, paired: true};
  }
  if (["status", "commit", "cancel"].includes(message.action)) {
    const {pending} = await chrome.storage.session.get("pending");
    if (!pending) throw new Error("没有待确认导入，请重新读取。");
    let result;
    try { result = await api(message.action, {id: pending, confirmed: message.confirmed === true}); }
    catch (error) {
      if (message.action !== "cancel" || error.status !== 409) throw error;
      result = {message: "待确认导入已结束或过期，可重新读取。"};
    }
    if (message.action !== "status") await chrome.storage.session.remove("pending");
    return {...result, paired: true};
  }
  throw new Error("不支持的操作。");
}

let working = false;
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) return false;
  if (working) { sendResponse({error: "正在处理，请稍候。"}); return false; }
  working = true;
  dispatch(message).then(result => {
    working = false;
    sendResponse({result});
  }, async error => {
    const {connection} = await chrome.storage.local.get("connection");
    working = false;
    sendResponse({error: error.message, paired: Boolean(connection)});
  })
    .finally(() => { working = false; });
  return true;
});

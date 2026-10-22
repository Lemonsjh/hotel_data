"use strict";
const $ = id => document.getElementById(id);
const labels = {me_cookie:"ME 主站", eb_cookie:"EB 经营后台", promotion_cookie:"推广后台",
  highlights_cookie:"酒店亮点", public_welfare_cookie:"公益流量"};
let timer, active = false;
$("version").textContent = "HOTEL COLLECTOR · 本机同步 · " + chrome.runtime.getManifest().version;

async function send(action, extra = {}) {
  const response = await chrome.runtime.sendMessage({action, ...extra});
  if (response?.error) {
    const error = new Error(response.error); error.paired = response.paired; throw error;
  }
  if (!response?.result) throw new Error("扩展后台未响应，请重新打开扩展。");
  return response.result;
}

function render(result) {
  clearTimeout(timer);
  $("pairing").hidden = Boolean(result.paired);
  $("connected").hidden = !result.paired;
  active = ["checking", "ready"].includes(result.state);
  $("preview").disabled = active;
  $("preview").textContent = result.state === "failed" ? "重新读取并核对酒店" : "读取 Cookie 并核对酒店";
  $("cancel").hidden = !["checking", "ready", "failed"].includes(result.state);
  const ready = result.state === "ready";
  $("hotel").hidden = !ready;
  $("hotel-name").textContent = result.hotel_name || "";
  $("poi").textContent = "门店 ID：" + (result.poi_id || "");
  $("confirm-box").hidden = !ready;
  $("commit").hidden = !ready;
  $("confirmed").checked = false;
  $("commit").disabled = true;
  $("groups").replaceChildren();
  if (result.groups) for (const [key, name] of Object.entries(labels)) {
    const row = document.createElement("div"); row.className = "cookie-row";
    const title = document.createElement("span"); title.textContent = name;
    const state = document.createElement("span");
    state.textContent = !Object.hasOwn(result.groups, key) ? "读取记录未返回" : result.groups[key] ?
      (result.state === "failed" ? "已读取 · 未保存" : result.state === "saved" ? "已读取 · 已保存" :
      "已读取 · 未验证") : "未读取 · 不覆盖";
    row.append(title, state); $("groups").append(row);
  }
  $("message").textContent = result.state === "failed" && !result.failure_code ?
    "酒店核对失败；当前服务未提供失败详情，请重启采集面板以加载新版诊断。原配置未修改。" :
    result.message || (result.paired ? "已连接，可读取当前浏览器登录状态。" : "请先完成本机配对。");
  $("message").dataset.state = result.state || "";
  renderDiagnostics(result);
  if (result.state === "checking") timer = setTimeout(() => perform("status"), 1500);
}

function renderDiagnostics(result) {
  $("diagnostic").hidden = result.state !== "failed";
  const details = result.failure_details || {};
  const phases = {browser_start:"启动核对浏览器", cookie_setup:"载入隔离会话", page_load:"加载评价页面",
    page_read:"读取页面内容", storage_read:"读取页面存储", page_title:"读取页面标题", completed:"页面核对结束",
    ordinary_browser:"当前普通浏览器核对"};
  const lines = [];
  if (result.failure_code) lines.push("原因编号：" + result.failure_code);
  if (phases[details.phase]) lines.push("阶段：" + phases[details.phase]);
  if (details.page_http_status) lines.push("页面 HTTP：" + details.page_http_status);
  if (typeof details.login_redirect === "boolean") lines.push("实际跳转到登录页：" + (details.login_redirect ? "是" : "否"));
  if (details.http_status) lines.push("评价接口 HTTP：" + details.http_status);
  if (details.business_code !== undefined) lines.push("评价接口业务码：" + details.business_code);
  const hints = {invalid_parameters:"请求参数", signature_rejected:"签名校验", login_required:"登录状态",
    manual_verification:"人工验证或访问频率", access_denied:"访问权限"};
  if (hints[details.business_hint]) lines.push("接口提示类别：" + hints[details.business_hint]);
  if (details.network_code) lines.push("网络状态：" + details.network_code);
  if (details.expected_poi_id) lines.push("当前配置门店 ID：" + details.expected_poi_id);
  if (details.detected_poi_id) lines.push("本次识别门店 ID：" + details.detected_poi_id);
  $("diagnostic-details").textContent = lines.join("\n");
}

async function perform(action, extra = {}) {
  clearTimeout(timer);
  if (action === "preview") {
    $("diagnostic").hidden = true;
    $("message").textContent = "正在当前普通浏览器的评价页核对酒店，请稍候…";
  }
  for (const button of document.querySelectorAll("button")) button.disabled = true;
  try {
    const result = await send(action, extra);
    if (action === "pair") $("pair-code").value = "";
    render(result);
  } catch (error) {
    if (error.paired === false) render({paired: false});
    $("message").textContent = error.message;
  }
  finally {
    $("pair").disabled = false;
    $("preview").disabled = active;
    $("cancel").disabled = false;
    $("commit").disabled = !$("confirmed").checked;
  }
}
$("pair").addEventListener("click", () => perform("pair", {code:$("pair-code").value, port:$("port").value}));
$("preview").addEventListener("click", () => perform("preview"));
$("cancel").addEventListener("click", () => perform("cancel"));
$("confirmed").addEventListener("change", () => { $("commit").disabled = !$("confirmed").checked; });
$("commit").addEventListener("click", () => perform("commit", {confirmed:$("confirmed").checked}));
perform("resume");

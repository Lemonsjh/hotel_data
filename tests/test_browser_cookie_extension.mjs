import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const base = new URL("../extensions/meituan-login-sync/", import.meta.url);
const cookiesSource = await readFile(new URL("cookies.js", base), "utf8");
const workerSource = await readFile(new URL("worker.js", base), "utf8");
const verifySource = await readFile(new URL("browser_verify.js", base), "utf8");
const manifest = JSON.parse(await readFile(new URL("manifest.json", base), "utf8"));
const id = "a".repeat(32);
const cookie = (name, domain, extra = {}) => ({name, value:"private-test", domain, hostOnly:true, path:"/", storeId:"0", ...extra});
let local = {}, session = {}, listener, reads = 0, requests = [], handler, panelBehavior;
const storage = object => ({
  get: async key => ({[key]:object[key]}),
  set: async values => { Object.assign(object, values); },
  remove: async key => { delete object[key]; }
});
const sourceCookies = [cookie("mebsid", "me.meituan.com"), cookie("ebbsid", "eb.meituan.com"),
  cookie("ads", "ebmidas.dianping.com"), cookie("tdc", "tdc.meituan.com"), cookie("gy", "gongyi.meituan.com")];
const chrome = {
  sidePanel: {setPanelBehavior: async behavior => { panelBehavior = behavior; }},
  runtime: {id, getURL:path => `chrome-extension://${id}/${path}`, getManifest:() => manifest,
    onMessage:{addListener:fn => { listener = fn; }}},
  storage: {local:storage(local), session:storage(session)},
  tabs: {query:async () => [{id:42, incognito:false, active:true,
    url:"https://me.meituan.com/ebooking/merchant/comment-manage-react#/home"}]},
  scripting: {executeScript:async options => {
    assert.equal(options.world, "MAIN");
    assert.equal(options.target.tabId, 42);
    return [{frameId:0, result:{challenge:options.args[0], source:"ordinary_browser_v1"}}];
  }},
  cookies: {
    getAllCookieStores:async () => [{id:"0", tabIds:[42]}],
    getAll:async options => {
      reads++;
      assert.equal(["meituan.com", "dianping.com"].includes(options.domain), true);
      return sourceCookies.filter(cookie => cookie.domain.endsWith(options.domain));
    }
  }
};
const context = vm.createContext({chrome, AbortSignal, console,
  fetch:async (url, options) => { requests.push({url, options}); return handler(url, options); }});
const cookieModule = new vm.SourceTextModule(cookiesSource, {context});
await cookieModule.link(() => { throw new Error("Unexpected import"); });
await cookieModule.evaluate();
const {groupCookies, matches} = cookieModule.namespace;
assert.equal(matches(cookie("x", ".meituan.com", {hostOnly:false}), "me.meituan.com"), true);
assert.equal(matches(cookie("x", "evilmeituan.com", {hostOnly:false}), "me.meituan.com"), false);
assert.equal(groupCookies(sourceCookies, []).me_cookie.length, 1);
assert.equal(groupCookies([cookie("secure", ".meituan.com", {hostOnly:false, secure:true,
  httpOnly:true, sameSite:"lax"}), ...sourceCookies], []).me_cookie[0].sameSite, "lax");
assert.throws(() => groupCookies([...sourceCookies, cookie("mebsid", ".meituan.com", {hostOnly:false, value:"different"})], []), /同名/);
assert.throws(() => groupCookies(sourceCookies, [cookie("x", "me.meituan.com", {partitionKey:{topLevelSite:"https://meituan.com"}})]), /分区/);
assert.throws(() => groupCookies(sourceCookies.filter(c => c.name !== "mebsid"), []), /ME/);
assert.throws(() => groupCookies([cookie("uuid", ".meituan.com", {hostOnly:false})], []), /mebsid/);
const commonOptional = groupCookies([...sourceCookies.filter(c => c.name !== "tdc"),
  cookie("uuid", ".meituan.com", {hostOnly:false})], []);
assert.equal(commonOptional.highlights_cookie.length, 0);
assert.throws(() => groupCookies(sourceCookies.map(c => ({...c, expirationDate:1})), []), /ME/);
assert.equal(groupCookies([...sourceCookies, cookie("ignored", "unrelated.example")], []).me_cookie.length, 1);
assert.deepEqual([...manifest.permissions].sort(), ["cookies", "scripting", "sidePanel", "storage"]);
assert.equal(manifest.action.default_popup, undefined);
assert.equal(manifest.side_panel.default_path, "popup.html");
assert.equal(manifest.incognito, "not_allowed");
assert.equal(manifest.host_permissions.length, 15);
for (const host of Object.values(cookieModule.namespace.HOSTS)) {
  assert.equal(manifest.host_permissions.includes("http://" + host + "/*"), true);
  assert.equal(manifest.host_permissions.includes("https://" + host + "/*"), true);
}
assert.equal(manifest.host_permissions.some(host => host.includes("*.")), false);
assert.equal(manifest.content_scripts, undefined);
assert.equal(manifest.externally_connectable, undefined);
const worker = new vm.SourceTextModule(workerSource, {context});
const verifyModule = new vm.SourceTextModule(verifySource, {context});
await verifyModule.link(() => { throw new Error("Unexpected import"); });
await verifyModule.evaluate();
await worker.link(specifier => {
  if (specifier === "./browser_verify.js") return verifyModule;
  assert.equal(specifier, "./cookies.js"); return cookieModule;
});
await worker.evaluate();
assert.equal(panelBehavior.openPanelOnActionClick, true);
const sender = {id, url:`chrome-extension://${id}/popup.html`};
async function send(message) {
  return new Promise(resolve => listener(message, sender, resolve));
}
function reply(data, status = 200) { return {ok:status === 200, status, json:async () => data}; }
handler = () => reply({token:"test-only-token"});
assert.equal(listener({action:"preview"}, {id:"other", url:sender.url}, () => {}), false);
assert.equal(listener({action:"preview"}, {id, url:"https://evil.example"}, () => {}), false);
assert.equal(reads, 0);
assert.equal((await send({action:"resume"})).result.paired, false);
assert.equal(reads, 0);
assert.match((await send({action:"pair", code:"0".repeat(32), port:"https://evil.example"})).error, /端口/);
assert.equal(requests.length, 0);
assert.equal((await send({action:"pair", code:"0".repeat(32), port:8765})).result.paired, true);
assert.equal(reads, 0);
handler = url => reply({id:"preview-id", state:url.endsWith("/verify") ? "ready" : "checking", verification_mode:"ordinary_browser_v1"});
await send({action:"preview"});
assert.equal(reads, 8);
assert.equal(session.pending, "preview-id");
assert.equal(JSON.stringify(local).includes("private-test"), false);
assert.equal(JSON.stringify(session).includes("private-test"), false);
const request = requests.find(r => r.url.endsWith("/preview"));
assert.equal(request.url, "http://127.0.0.1:8765/api/browser-cookie/preview");
assert.equal(request.options.redirect, "error");
assert.equal(request.options.credentials, "omit");
assert.equal(request.options.headers.Authorization, "Bearer test-only-token");
handler = () => reply({id:"preview-id", state:"ready"});
await send({action:"status"});
assert.equal(reads, 8); // Local polling never re-reads Cookie or requests Meituan.
handler = () => reply({state:"saved"});
await send({action:"commit", confirmed:true});
assert.equal(session.pending, undefined);
assert.equal(reads, 8);
assert.equal(requests.every(r => new URL(r.url).hostname === "127.0.0.1"), true);
const readSummary = {me_cookie:true, eb_cookie:true, promotion_cookie:true, highlights_cookie:true, public_welfare_cookie:false};
const failed = {id:"failed-id", state:"failed", groups:readSummary, failure_code:"review_business_rejected",
  failure_details:{phase:"completed", http_status:200, business_code:"303", login_redirect:false}, message:"Cookie 已读取；评价接口未返回业务成功。原配置未修改。"};
handler = () => reply(failed);
assert.equal((await send({action:"preview"})).result.failure_code, "review_business_rejected");
const readsAfterFailure = reads;
const resumed = await send({action:"resume"});
assert.equal(resumed.result.paired, true);
assert.equal(resumed.result.groups.me_cookie, true);
assert.equal(resumed.result.groups.public_welfare_cookie, false);
assert.equal(reads, readsAfterFailure); // Reopening only reads local status, not browser Cookie.
handler = url => reply(url.endsWith("/status") ? failed : url.endsWith("/cancel") ? {message:"cancelled"} :
  {id:"retry-id",state:url.endsWith("/verify") ? "ready" : "checking", verification_mode:"ordinary_browser_v1"});
assert.equal((await send({action:"preview"})).result.id, "retry-id");
assert.equal(reads, readsAfterFailure + 8); // Refresh values after one ordinary-browser check.
handler = () => reply({message:"cancelled"});
await send({action:"cancel"});
handler = () => reply({error:"授权已失效"}, 403);
const invalid = await send({action:"preview"});
assert.equal(invalid.paired, false);
assert.equal(local.connection, undefined);
assert.equal((await send({action:"unknown"})).error, "不支持的操作。");

// Exercise the actual sidebar renderer, independent of Cookie values and server state.
const nodes = new Map();
function element() {
  return {hidden:false, disabled:false, checked:false, textContent:"", children:[], dataset:{},
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); }, addEventListener() {}};
}
function node(name) { if (!nodes.has(name)) nodes.set(name, element()); return nodes.get(name); }
const ui = vm.createContext({console, Object, setTimeout:() => 1, clearTimeout:() => {},
  chrome:{runtime:{sendMessage:async () => ({result:{paired:true}}), getManifest:() => manifest}},
  document:{getElementById:node, createElement:element,
    querySelectorAll:() => ["pair", "preview", "cancel", "commit"].map(node)}});
vm.runInContext(await readFile(new URL("popup.js", base), "utf8"), ui);
await new Promise(resolve => setImmediate(resolve));
ui.fixture = {...failed, paired:true};
vm.runInContext("render(fixture)", ui);
assert.equal(node("connected").hidden, false);
assert.equal(node("pairing").hidden, true);
assert.equal(node("groups").children[0].children[1].textContent, "已读取 · 未保存");
assert.equal(node("groups").children[4].children[1].textContent, "未读取 · 不覆盖");
assert.equal(node("diagnostic").hidden, false);
assert.match(node("diagnostic-details").textContent, /业务码：303/);
assert.match(node("diagnostic-details").textContent, /实际跳转到登录页：否/);
assert.equal(node("preview").disabled, false);
assert.equal(node("preview").textContent, "重新读取并核对酒店");
assert.equal(node("commit").hidden, true);
assert.equal(node("commit").disabled, true);
ui.fixture = {paired:true,state:"failed",groups:{}};
vm.runInContext("render(fixture)", ui);
assert.equal(node("groups").children[0].children[1].textContent, "读取记录未返回");
assert.match(node("message").textContent, /重启采集面板/);

// The page check uses one fresh protected request in the existing page session.
let pageCalls = 0, pageStatus = 200, pageCode = 10000;
const signed = "https://me.meituan.com/api/gw/v1/base/comments/queryGeneralCommentInfo?poiId=123&partnerId=456&bizAccountId=789&offset=1&limit=20&platform=2&csecversion=4.3.0&mtgsig=fresh";
let entries = [{name:signed, startTime:1}, {name:signed.replace("offset=1", "offset=0"), startTime:2}];
const pageContext = vm.createContext({URL, AbortSignal, Date,
  location:{origin:"https://me.meituan.com", pathname:"/ebooking/merchant/comment-manage-react"},
  performance:{now:() => 100, getEntriesByType:() => entries},
  fetch:async (url, options) => {
    pageCalls++;
    assert.equal(new URL(url).searchParams.has("mtgsig"), false);
    assert.equal(new URL(url).searchParams.get("offset"), "1");
    assert.equal(new URL(url).searchParams.get("limit"), "20");
    assert.equal(new URL(url).searchParams.get("platform"), "2");
    assert.equal(new URL(url).searchParams.get("csecversion"), "4.3.0");
    assert.equal(options.credentials, "include");
    return {status:pageStatus, url:signed, json:async () => ({code:pageCode, msg:"请求参数错误 PRIVATE-NOT-FOR-EXPORT",
      data:{poiName:"测试酒店", guestPhone:"PRIVATE-NOT-FOR-EXPORT"}})};
  }});
const pageModule = new vm.SourceTextModule(verifySource, {context:pageContext});
await pageModule.link(() => { throw new Error("Unexpected import"); });
await pageModule.evaluate();
const proof = await pageModule.namespace.verifyInPage("challenge");
assert.equal(pageCalls, 1);
assert.equal(proof.challenge, "challenge");
assert.equal(proof.hotel_name, "测试酒店");
assert.equal(proof.review_detail_url, signed);
assert.equal(JSON.stringify(proof).includes("PRIVATE"), false);
pageCode = 1000;
const rejectedProof = await pageModule.namespace.verifyInPage("challenge");
assert.equal(rejectedProof.business_code, "1000");
assert.equal(rejectedProof.business_hint, "invalid_parameters");
assert.equal(JSON.stringify(rejectedProof).includes("PRIVATE"), false);
entries = [];
assert.equal((await pageModule.namespace.verifyInPage("challenge")).failure_code, "ordinary_request_missing");
assert.equal(pageCalls, 2);
console.log("Extension tests: Cookie scopes, profile store, CHIPS, pairing, import, redaction and permissions passed.");

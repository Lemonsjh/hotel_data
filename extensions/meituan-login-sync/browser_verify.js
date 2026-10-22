const REVIEW_PAGE = "https://me.meituan.com/ebooking/merchant/comment-manage-react";

// This function is copied into the already signed-in page. Keep it self-contained.
export async function verifyInPage(challenge) {
  const endpoint = "/api/gw/v1/base/comments/queryGeneralCommentInfo";
  const base = {challenge, checked_at:Date.now(), source:"ordinary_browser_v1"};
  const failure = code => ({...base, failure_code:code});
  const isPage = () => location.origin === "https://me.meituan.com" &&
    location.pathname === "/ebooking/merchant/comment-manage-react";
  if (!isPage()) return failure("ordinary_page_missing");
  const identity = value => {
    try {
      const url = new URL(value);
      if (url.origin !== location.origin || url.pathname !== endpoint || !url.searchParams.get("mtgsig")) return null;
      if (!["poiId", "partnerId", "bizAccountId"].every(key => /^\d{1,24}$/.test(url.searchParams.get(key) || ""))) return null;
      return url;
    } catch (_) { return null; }
  };
  const observed = performance.getEntriesByType("resource").map(item => identity(item.name)).filter(url => {
    if (!url) return false;
    // Ignore the invalid zero-based request emitted by extension 0.2.0.
    return ["offset", "limit"].every(key => !url.searchParams.has(key) ||
      /^[1-9]\d*$/.test(url.searchParams.get(key)));
  }).at(-1);
  if (!observed) return failure("ordinary_request_missing");
  // The page's existing request code generates a fresh signature, as in the collector.
  const request = new URL(observed.href);
  request.searchParams.delete("mtgsig");
  let response, data;
  const started = performance.now();
  try {
    response = await fetch(request.href, {credentials:"include", redirect:"error",
      signal:AbortSignal.timeout(15000), headers:{"X-Requested-With":"XMLHttpRequest", "Request-Page-Source":"ME"}});
    if (!isPage()) return failure("ordinary_page_changed");
    try { data = await response.json(); } catch (_) { data = null; }
  } catch (_) { return failure("ordinary_request_failed"); }
  let signed = identity(response.url);
  if (!signed) {
    signed = performance.getEntriesByType("resource").filter(item => item.startTime >= started)
      .map(item => identity(item.name)).filter(Boolean).at(-1);
  }
  if (signed && ["poiId", "partnerId", "bizAccountId"].some(key =>
    signed.searchParams.get(key) !== observed.searchParams.get(key))) return failure("ordinary_page_changed");
  const names = new Set();
  let remaining = 200;
  function visit(value) {
    if (--remaining < 0 || !value || typeof value !== "object") return;
    for (const [key, item] of Object.entries(value).slice(0, 80)) {
      if (/^(poiName|hotelName)$/i.test(key) && typeof item === "string") {
        const name = item.replace(/\s+/g, " ").trim();
        if (name.length >= 3 && name.length <= 110 && /酒店|宾馆|民宿|客栈|电竞|公寓|旅馆|Hotel/i.test(name) && !/[{}<>\x00-\x1f]/.test(name)) names.add(name);
      }
      if (typeof item === "object") visit(item);
    }
  }
  if (response.status === 200 && data?.code === 10000) visit(data.data);
  const message = [data?.msg, data?.message].filter(value => typeof value === "string").join(" ").slice(0, 300);
  const hints = [["invalid_parameters", /参数|入参|invalid.{0,15}param|param.{0,15}invalid/i],
    ["signature_rejected", /签名|signature/i], ["login_required", /未登录|请先登录|登录失效|登录过期|login/i],
    ["manual_verification", /验证|风控|频繁|captcha|too many|risk/i],
    ["access_denied", /无权|权限不足|禁止访问|permission|denied/i]];
  return {...base, checked_at:Date.now(), http_status:response.status,
    business_code:/^-?\d{1,6}$/.test(String(data?.code)) ? String(data.code) : null,
    business_hint:data?.code === 10000 ? null : hints.find(([, pattern]) => pattern.test(message))?.[0] || null,
    response_valid:!!data?.data && typeof data.data === "object" && !Array.isArray(data.data),
    review_detail_url:signed?.href || "", hotel_name:names.size === 1 ? [...names][0] : ""};
}

export async function verifyCurrentSession(challenge) {
  try {
    const tabs = await chrome.tabs.query({currentWindow:true, url:REVIEW_PAGE + "*"});
    const eligible = tabs.filter(tab => !tab.incognito && tab.url?.split(/[?#]/)[0] === REVIEW_PAGE);
    const selected = eligible.find(tab => tab.active) || (eligible.length === 1 ? eligible[0] : null);
    if (!selected) return {challenge, source:"ordinary_browser_v1", checked_at:Date.now(), failure_code:"ordinary_page_missing"};
    const results = await chrome.scripting.executeScript({target:{tabId:selected.id}, world:"MAIN",
      func:verifyInPage, args:[challenge]});
    return results.find(item => item.frameId === 0)?.result ||
      {challenge, source:"ordinary_browser_v1", checked_at:Date.now(), failure_code:"ordinary_request_failed"};
  } catch (_) {
    return {challenge, source:"ordinary_browser_v1", checked_at:Date.now(), failure_code:"ordinary_script_unavailable"};
  }
}

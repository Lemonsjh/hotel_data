export const HOSTS = {
  me_cookie: "me.meituan.com", eb_cookie: "eb.meituan.com",
  promotion_cookie: "ebmidas.dianping.com", highlights_cookie: "tdc.meituan.com",
  public_welfare_cookie: "gongyi.meituan.com"
};
const REQUIRED = {me_cookie:"mebsid", eb_cookie:"ebbsid"};
const COMMON_ONLY = new Set(["uuid", "__rcf___", "__rcf_com.sankuai.fetalos.web.hotelfeme__"]);

export function matches(cookie, host) {
  const domain = cookie.domain.replace(/^\./, "").toLowerCase();
  return host === domain || (!cookie.hostOnly && host.endsWith("." + domain));
}

export function groupCookies(cookies, partitioned, now = Date.now() / 1000) {
  const groups = {};
  for (const [key, host] of Object.entries(HOSTS)) {
    if (partitioned.some(cookie => cookie.partitionKey && matches(cookie, host))) {
      throw new Error("检测到分区 Cookie，第一版不支持安全转换，请使用手动配置。");
    }
    let selected = cookies.filter(cookie => matches(cookie, host) &&
      (cookie.expirationDate === undefined || cookie.expirationDate > now));
    const values = new Map();
    for (const cookie of selected) {
      if (cookie.partitionKey) throw new Error("暂不支持分区 Cookie，请使用手动配置。");
      if (values.has(cookie.name) && values.get(cookie.name) !== cookie.value) {
        throw new Error("存在同名但不同值的 Cookie，不能安全转换，请使用手动配置。");
      }
      values.set(cookie.name, cookie.value);
    }
    if (REQUIRED[key] && !values.get(REQUIRED[key])) {
      throw new Error(`未读取到 ${key === "me_cookie" ? "ME 的 mebsid" : "EB 的 ebbsid"} 登录 Cookie，请重新加载 0.2.2 扩展并检查相应网站访问权限。`);
    }
    if (!REQUIRED[key] && selected.length && selected.every(cookie => COMMON_ONLY.has(cookie.name))) selected = [];
    groups[key] = selected.map(({name, value, domain, path, hostOnly, storeId, expirationDate, secure, httpOnly, sameSite}) =>
      ({name, value, domain, path, hostOnly, storeId, secure, httpOnly, sameSite,
        ...(expirationDate === undefined ? {} : {expirationDate})}));
  }
  if (!groups.me_cookie.length || !groups.eb_cookie.length) {
    throw new Error("未读到 ME 或 EB Cookie，请在同一普通浏览器配置文件中先完成登录。");
  }
  return groups;
}

export async function readCurrentProfile() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || tab.incognito) throw new Error("请在普通浏览器窗口中使用扩展。");
  const stores = await chrome.cookies.getAllCookieStores();
  const store = stores.find(item => item.tabIds.includes(tab.id));
  if (!store) throw new Error("无法确定当前浏览器配置文件，请切回普通网页后重试。");
  const cookies = [], partitions = [];
  for (const domain of ["meituan.com", "dianping.com"]) {
    // Never enumerate localhost Cookie even though local fetch has a host permission.
    cookies.push(...await chrome.cookies.getAll({domain, storeId: store.id}));
    // Empty partition filter includes all partition keys. Never silently flatten CHIPS.
    partitions.push(...await chrome.cookies.getAll({domain, storeId: store.id, partitionKey: {}}));
  }
  return groupCookies(cookies, partitions);
}

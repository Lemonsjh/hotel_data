using System;
using System.Collections;
using System.Collections.Generic;
using System.Data;
using System.Reflection;
using System.Threading;

public static class HuiyunGuestQuery {
    public static object Execute(IDictionary sys, Assembly assembly, Type manager, string address, string hotel,
        string hotelName, string day, List<Dictionary<string, object>> records, Dictionary<string, string> roomTypes) {
        Type type = assembly.GetType("CshisNet.Client.Common.HistoryServiceClient", true);
        object proxy = Activator.CreateInstance(type);
        try {
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                new object[] { type.GetProperty("Url").GetValue(proxy, null), address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            var rows = new List<Dictionary<string, object>>();
            var stays = new Dictionary<string, Dictionary<string, object>>();
            var guestIds = new HashSet<string>();
            foreach (var source in records) {
                string roomType = Convert.ToString(source["rm_typ"]).Trim();
                if (roomType == "" && Convert.ToString(source["AcctStus"]).Trim() == "团队") continue;
                string account = Convert.ToString(source["acct_num"]).Trim();
                string guest = Convert.ToString(source["gh_num"]).Trim();
                if (account == "" || guest == "") throw new InvalidOperationException("客人账户或编号缺失");
                if (!stays.ContainsKey(account)) {
                    var detail = (DataSet)type.GetMethod("GetAccountInfo").Invoke(proxy, new object[] { hotel, account });
                    if (detail == null || !detail.Tables.Contains("account") || detail.Tables["account"].Rows.Count != 1)
                        throw new InvalidOperationException("客人实际入住离店详情缺失");
                    DataRow row = detail.Tables["account"].Rows[0];
                    if (Convert.ToString(row["htl_cd"]).Trim() != hotel || Convert.ToString(row["acct_num"]).Trim() != account)
                        throw new InvalidOperationException("客人实际住店详情酒店或账户不符");
                    var times = new Dictionary<string, object>();
                    foreach (string field in new[] { "chkin_dt", "chko_dt" }) {
                        if (row[field] == DBNull.Value) throw new InvalidOperationException("客人实际入住离店时间缺失");
                        times[field] = ((DateTime)row[field]).ToString("yyyy-MM-dd HH:mm:ss");
                    }
                    stays[account] = times;
                    Thread.Sleep(300);
                }
                var item = new Dictionary<string, object>(source);
                foreach (var pair in stays[account]) item[pair.Key] = pair.Value;
                rows.Add(item);
                guestIds.Add(guest);
            }
            var profiles = new Dictionary<string, object>();
            var pending = new List<string>(guestIds);
            for (int offset = 0; offset < pending.Count; offset += 100) {
                string[] batch = pending.GetRange(offset, Math.Min(100, pending.Count - offset)).ToArray();
                var data = (DataSet)type.GetMethod("GetHistory").Invoke(proxy, new object[] { hotel, batch });
                if (data == null || !data.Tables.Contains("history")) throw new InvalidOperationException("客人资料缺失");
                foreach (DataRow row in data.Tables["history"].Rows) {
                    string id = Convert.ToString(row["gh_num"]).Trim();
                    if (!guestIds.Contains(id)) continue;
                    if (profiles.ContainsKey(id)) throw new InvalidOperationException("客人资料编号重复");
                    profiles[id] = new Dictionary<string, object> {
                        { "name", row["gh_nm"] == DBNull.Value ? null : Convert.ToString(row["gh_nm"]).Trim() },
                        { "id_card", row["crtf_num"] == DBNull.Value ? null : Convert.ToString(row["crtf_num"]).Trim() }
                    };
                }
                Thread.Sleep(300);
            }
            if (profiles.Count != guestIds.Count) throw new InvalidOperationException("部分客人资料未返回，停止写入");
            if (hotel != Convert.ToString(sys["CurrentHotel"])) throw new InvalidOperationException("查询期间酒店已切换");
            return new { ok = true, hotel_code = hotel, hotel_name = hotelName, departure_date = day,
                room_types = roomTypes, records = rows, profiles };
        } finally { ((IDisposable)proxy).Dispose(); }
    }
}

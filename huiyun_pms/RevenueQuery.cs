using System;
using System.Collections;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;

public static class HuiyunRevenueQuery {
    private static object Proxy(Assembly assembly, Type manager, IDictionary sys, string service, string address, string hotel) {
        Type type = assembly.GetType("CshisNet.Client.Common." + service, true);
        object proxy = Activator.CreateInstance(type);
        try {
            type.GetProperty("Url").SetValue(proxy, manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) })
                .Invoke(null, new object[] { type.GetProperty("Url").GetValue(proxy, null), address }), null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            return proxy;
        } catch { ((IDisposable)proxy).Dispose(); throw; }
    }

    private static object Call(object proxy, string method, params object[] args) {
        return proxy.GetType().GetMethod(method).Invoke(proxy, args);
    }

    private static Dictionary<string, object> Copy(DataRow row, string[] fields) {
        var result = new Dictionary<string, object>();
        foreach (string field in fields) {
            if (!row.Table.Columns.Contains(field)) throw new InvalidOperationException("房费字段缺失：" + field);
            object value = row[field];
            result[field] = value == DBNull.Value ? null : value is DateTime ? ((DateTime)value).ToString("yyyy-MM-dd HH:mm:ss") :
                value is decimal ? ((decimal)value).ToString(CultureInfo.InvariantCulture) : value;
        }
        return result;
    }

    public static object Execute(IDictionary sys, string home, string hotel, string hotelName, string address, string[] args) {
        DateTime start = DateTime.ParseExact(args[1], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        DateTime end = DateTime.ParseExact(args[2], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (start > end || (end - start).TotalDays > 29 || end >= DateTime.Now.Date)
            throw new InvalidOperationException("房费查询必须为最多30个已完成营业日");
        object oper = ((IDictionary)sys["SystemOper"])[hotel];
        string buildings = Convert.ToString(oper.GetType().GetMethod("GetIniItem", new[] { typeof(string), typeof(string) })
            .Invoke(oper, new object[] { "基本参数", "可操作楼区" })).Trim();
        if (buildings == "") throw new InvalidOperationException("无可操作楼区");
        var assembly = Assembly.LoadFrom(Path.Combine(home, "CshisNetClientWebProxy.dll"));
        var manager = Assembly.LoadFrom(Path.Combine(home, "CAS.dll")).GetType("GenericFrameWork.CAS.URLManager", true);
        var functions = Assembly.LoadFrom(Path.Combine(home, "CshisNetBasicCommon.dll")).GetType("CshisNet.BasicCommon.PublicFunctions", true);
        var decompress = functions.GetMethod("UnCompressDataSet", new[] { typeof(byte[]) });
        object proxy = Proxy(assembly, manager, sys, "MiscServiceClient", address, hotel);
        var transactions = new List<Dictionary<string, object>>();
        var ids = new HashSet<string>();
        decimal amount = 0, nights = 0, expectedAmount = 0, expectedNights = 0;
        try {
            Type optionType = assembly.GetType("CshisNet.Client.Common.MiscService.TransactSearchOption", true);
            object option = Activator.CreateInstance(optionType);
            foreach (var property in optionType.GetProperties()) if (property.PropertyType == typeof(string)) property.SetValue(option, "", null);
            optionType.GetProperty("fromDate").SetValue(option, start, null);
            optionType.GetProperty("toDate").SetValue(option, end, null);
            optionType.GetProperty("bldNo").SetValue(option, buildings.Replace(",", "','"), null);
            optionType.GetProperty("trnNo").SetValue(option, "100", null);
            optionType.GetProperty("trnAmt").SetValue(option, -99999999m, null);
            optionType.GetProperty("trnAmtTop").SetValue(option, 99999999m, null);
            for (int page = 0; ; page++) {
                if (page >= 200) throw new InvalidOperationException("房费分页超过上限");
                object[] parameters = { option, page * 500 + 1, (page + 1) * 500, "trn_dt,post_tm,trn_id", hotel, false, 0m };
                var data = (DataSet)decompress.Invoke(null, new[] { proxy.GetType().GetMethod("GetTransactList").Invoke(proxy, parameters) });
                if (data == null || !data.Tables.Contains("TranTable") || !data.Tables.Contains("totalamt") || data.Tables["totalamt"].Rows.Count != 1)
                    throw new InvalidOperationException("房费明细或汇总缺失");
                decimal pageAmount = Convert.ToDecimal(data.Tables["totalamt"].Rows[0]["totalamt"]);
                decimal pageNights = Convert.ToDecimal(data.Tables["totalamt"].Rows[0]["fnights"]);
                if (page == 0) { expectedAmount = pageAmount; expectedNights = pageNights; }
                else if (pageAmount != expectedAmount || pageNights != expectedNights) throw new InvalidOperationException("分页期间房费已变化");
                foreach (DataRow row in data.Tables["TranTable"].Rows) {
                    var item = Copy(row, new[] { "trn_id", "trn_dt", "trn_cd", "trn_drpt", "trn_amt", "financenights", "rm_num", "acct_num", "orgacct_num", "trn_oper" });
                    if (!ids.Add(Convert.ToString(item["trn_id"])))
                        throw new InvalidOperationException("房费分页交易ID重复：第" + page + "页，本页" + data.Tables["TranTable"].Rows.Count + "笔，累计" + transactions.Count + "笔");
                    if (row["trn_amt"] == DBNull.Value || row["financenights"] == DBNull.Value) throw new InvalidOperationException("房费金额或间夜缺失");
                    amount += Convert.ToDecimal(row["trn_amt"]); nights += Convert.ToDecimal(row["financenights"]);
                    transactions.Add(item);
                }
                if (data.Tables["TranTable"].Rows.Count < 500) break;
                Thread.Sleep(500);
            }
        } finally { ((IDisposable)proxy).Dispose(); }
        if (amount != expectedAmount || nights != expectedNights) throw new InvalidOperationException("房费分页合计不完整");
        string[] accounts = transactions.SelectMany(row => new[] { Convert.ToString(row["acct_num"]).Trim(),
            Convert.ToString(row["orgacct_num"]).Trim() }).Where(id => id != "").Distinct().ToArray();
        var details = new Dictionary<string, object>();
        object casher = Proxy(assembly, manager, sys, "CasherServiceClient", address, hotel);
        object front = Proxy(assembly, manager, sys, "FrontServiceClient", address, hotel);
        object room = Proxy(assembly, manager, sys, "RoomServiceClient", address, hotel);
        var roomTypes = new Dictionary<string, string>();
        var rooms = new Dictionary<string, string>();
        try {
            Thread.Sleep(2000);
            var types = (DataSet)Call(room, "GetRoomtypUsing", hotel);
            foreach (DataRow row in types.Tables["sysconf"].Rows) roomTypes.Add(Convert.ToString(row["para_cd"]).Trim(), Convert.ToString(row["para_drpt"]).Trim());
            var encode = functions.GetMethod("EncodeString", new[] { typeof(string) });
            string allowed = buildings.Replace("'", "''").Replace(",", "','");
            Thread.Sleep(500);
            byte[] roomBytes = (byte[])Call(room, "GetRoomInfoDataCompress",
                encode.Invoke(null, new object[] { "1=1 and rmstus.bld_cd in ('" + allowed + "')" }),
                encode.Invoke(null, new object[] { "1=1 and houseblock.bld_cd in ('" + allowed + "')" }));
            var roomData = (DataSet)decompress.Invoke(null, new object[] { roomBytes });
            foreach (DataRow row in roomData.Tables["roomstus"].Rows) {
                string number = Convert.ToString(row["rm_num"]).Trim(), code = Convert.ToString(row["rm_typ"]).Trim();
                if (number == "" || number == "**") continue;
                if (rooms.ContainsKey(number) && rooms[number] != code) throw new InvalidOperationException("收费房号对应多个房型");
                rooms[number] = code;
            }
            for (int offset = 0; offset < accounts.Length; offset += 100) {
                string[] batch = accounts.Skip(offset).Take(100).ToArray();
                if (batch.Any(value => value == "")) throw new InvalidOperationException("房费账户号缺失");
                Thread.Sleep(500);
                var data = (DataSet)Call(casher, "GetAccountInfoList", hotel, batch);
                var selected = new Dictionary<string, Dictionary<string, object>>();
                foreach (DataRow row in data.Tables["acct"].Rows) {
                    var item = Copy(row, new[] { "acct_num", "htl_cd", "resv_num", "acct_stus", "rm_num", "rm_typ", "rt_cd", "chkin_dt", "chko_dt" });
                    string id = Convert.ToString(item["acct_num"]).Trim();
                    if (!batch.Contains(id) || Convert.ToString(item["htl_cd"]).Trim() != hotel || selected.ContainsKey(id))
                        throw new InvalidOperationException("房费账户酒店或身份不符");
                    selected.Add(id, item);
                }
                // 账户来自权限过滤后的收费记录；团账主账户无楼区，不再重复用账户楼区过滤。
                string condition = "account.acct_num in ('" + String.Join("','", batch.Select(value => value.Replace("'", "''")).ToArray()) + "')";
                Thread.Sleep(500);
                byte[] bytes = (byte[])Call(front, "GetAccountData", "", 1, 101, encode.Invoke(null, new object[] { "account.acct_num" }),
                    encode.Invoke(null, new object[] { condition }), hotel);
                var names = (DataSet)decompress.Invoke(null, new object[] { bytes });
                var named = new HashSet<string>();
                foreach (DataRow row in names.Tables["Account"].Rows) {
                    string id = Convert.ToString(row["acct_num"]).Trim();
                    if (!selected.ContainsKey(id) || !named.Add(id)) throw new InvalidOperationException("房费客人资料关联不唯一");
                    selected[id]["guest_name"] = row["gh_nm"] == DBNull.Value ? null : Convert.ToString(row["gh_nm"]).Trim();
                    selected[id]["customer_source"] = row["TaNum"] == DBNull.Value ? null : Convert.ToString(row["TaNum"]).Trim();
                }
                if (selected.Count != batch.Length || named.Count != batch.Length)
                    throw new InvalidOperationException("部分房费账户资料缺失：请求" + batch.Length + "，账户" + selected.Count + "，客人" + named.Count);
                foreach (var pair in selected) details.Add(pair.Key, pair.Value);
            }
        } finally { ((IDisposable)casher).Dispose(); ((IDisposable)front).Dispose(); ((IDisposable)room).Dispose(); }
        if (hotel != Convert.ToString(sys["CurrentHotel"])) throw new InvalidOperationException("房费查询期间酒店已切换");
        return new { ok = true, hotel_code = hotel, hotel_name = hotelName, start_date = args[1], end_date = args[2],
            total_amount = amount.ToString(CultureInfo.InvariantCulture), total_nights = nights.ToString(CultureInfo.InvariantCulture),
            transactions, accounts = details, room_types = roomTypes, rooms };
    }

}

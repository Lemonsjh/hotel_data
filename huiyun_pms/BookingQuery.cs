using System;
using System.Collections;
using System.Collections.Generic;
using System.Data;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Threading;

public static class HuiyunBookingQuery {
    public static object Execute(IDictionary sys, string home, string hotel, string hotelName, string address, string command) {
        string[] args = command.Split('|');
        if (args.Length == 4 && args[3] == "revenue")
            return HuiyunRevenueQuery.Execute(sys, home, hotel, hotelName, address, args);
        // 沿用已登录启动器的查询通道，报表组件可热更新，无需重新登录。
        if (args.Length == 4 && (args[3] == "daily" || args[3] == "monthly" || args[3] == "room_daily" || args[3] == "room_monthly"))
            return HuiyunReportQuery.Execute(sys, home, hotel, hotelName, args);
        bool guests = args.Length == 4 && args[3] == "guests";
        if (args.Length != 3 && !guests) throw new InvalidOperationException("查询日期无效");
        DateTime start = DateTime.ParseExact(args[1], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        DateTime end = DateTime.ParseExact(args[2], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (end < start || (end - start).TotalDays > 29) throw new InvalidOperationException("预订查询范围无效");
        if (guests && start != end) throw new InvalidOperationException("客人明细每次查询一个离店日期");
        object oper = ((IDictionary)sys["SystemOper"])[hotel];
        string buildings = Convert.ToString(oper.GetType().GetMethod("GetIniItem", new[] { typeof(string), typeof(string) })
            .Invoke(oper, new object[] { "基本参数", "可操作楼区" }));
        if (String.IsNullOrWhiteSpace(buildings)) throw new InvalidOperationException("无可操作楼区");
        string allowed = buildings.Trim().Replace("'", "''").Replace(",", "','");
        string dateField = guests ? "account.chko_dt" : "account.arr_dt";
        string condition = dateField + " >= '" + start.ToString("yyyy-MM-dd") + "' and " + dateField + " < '" +
            end.AddDays(1).ToString("yyyy-MM-dd") + "' and account.bld_cd in ('" + allowed + "')";
        if (guests) condition += " and account.chkin_dt >= '1901-01-01'"; // 排除未入住的取消预订账户。
        var assembly = Assembly.LoadFrom(Path.Combine(home, "CshisNetClientWebProxy.dll"));
        var functions = Assembly.LoadFrom(Path.Combine(home, "CshisNetBasicCommon.dll"))
            .GetType("CshisNet.BasicCommon.PublicFunctions", true);
        var manager = Assembly.LoadFrom(Path.Combine(home, "CAS.dll")).GetType("GenericFrameWork.CAS.URLManager", true);
        var encode = functions.GetMethod("EncodeString", new[] { typeof(string) });
        var decompress = functions.GetMethod("UnCompressDataSet", new[] { typeof(byte[]) });
        Type type = assembly.GetType("CshisNet.Client.Common.FrontServiceClient", true);
        object proxy = Activator.CreateInstance(type);
        try {
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                new object[] { type.GetProperty("Url").GetValue(proxy, null), address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            var roomTypes = new Dictionary<string, string>();
            Type roomType = assembly.GetType("CshisNet.Client.Common.RoomServiceClient", true);
            object roomProxy = Activator.CreateInstance(roomType);
            try {
                string roomUrl = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                    new object[] { roomType.GetProperty("Url").GetValue(roomProxy, null), address });
                roomType.GetProperty("Url").SetValue(roomProxy, roomUrl, null);
                roomType.GetProperty("CookieContainer").SetValue(roomProxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
                roomType.GetProperty("Timeout").SetValue(roomProxy, 20000, null);
                var types = (DataSet)roomType.GetMethod("GetRoomtypUsing").Invoke(roomProxy, new object[] { hotel });
                if (types == null || !types.Tables.Contains("sysconf")) throw new InvalidOperationException("房型资料缺失");
                foreach (DataRow row in types.Tables["sysconf"].Rows) {
                    string key = Convert.ToString(row["para_cd"]).Trim();
                    if (!String.IsNullOrEmpty(key)) roomTypes.Add(key, Convert.ToString(row["para_drpt"]).Trim());
                }
            } finally { ((IDisposable)roomProxy).Dispose(); }
            Thread.Sleep(2000);
            var records = new List<Dictionary<string, object>>();
            var fields = new List<string> { "acct_num", "resv_num", "rm_typ", "rm_num", "rt_amt", "arr_dt", "dpt_dt",
                "rm_nums", "AcctStus", "shr_num", "shr_flg", "rt_cd" };
            if (guests) fields.AddRange(new[] { "gh_num", "TaNum" });
            for (int page = 0; page < 200; page++) {
                int from = page * 200 + 1, to = from + 199;
                byte[] bytes = (byte[])type.GetMethod("GetAccountData").Invoke(proxy,
                    new object[] { "", from, to, encode.Invoke(null, new object[] { dateField + ", account.acct_num" }),
                        encode.Invoke(null, new object[] { condition }), hotel });
                var data = (DataSet)decompress.Invoke(null, new object[] { bytes });
                if (data == null || !data.Tables.Contains("Account")) throw new InvalidOperationException("预订账户数据缺失");
                var table = data.Tables["Account"];
                foreach (string field in fields) if (!table.Columns.Contains(field)) throw new InvalidOperationException("预订字段缺失：" + field);
                foreach (DataRow source in table.Rows) {
                    var row = new Dictionary<string, object>();
                    foreach (string field in fields) {
                        object value = source[field];
                        row[field] = value == DBNull.Value ? null : value is DateTime ? ((DateTime)value).ToString("yyyy-MM-dd HH:mm:ss") : value;
                    }
                    records.Add(row);
                }
                if (table.Rows.Count < 200) {
                    if (guests) return HuiyunGuestQuery.Execute(sys, assembly, manager, address, hotel, hotelName, args[1], records, roomTypes);
                    return new { ok = true, hotel_code = hotel, hotel_name = hotelName,
                        start_date = args[1], end_date = args[2], room_types = roomTypes, records };
                }
                Thread.Sleep(500);
            }
            throw new InvalidOperationException("预订分页超过上限，停止写入");
        } finally { ((IDisposable)proxy).Dispose(); }
    }
}

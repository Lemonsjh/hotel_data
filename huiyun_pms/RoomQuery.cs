using System;
using System.Collections;
using System.Collections.Generic;
using System.Data;
using System.IO;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Threading;

public static class HuiyunRoomQuery {
    public static object Execute(IDictionary sys, string home, string command) {
        string hotel = Convert.ToString(sys["CurrentHotel"]);
        var pool = sys["CookieContainer"] as IDictionary;
        if (String.IsNullOrEmpty(hotel) || pool == null || !pool.Contains(hotel))
            throw new InvalidOperationException("当前酒店会话缺失");
        var cookies = pool[hotel] as CookieContainer;
        if (cookies == null || cookies.Count == 0) throw new InvalidOperationException("客户端未登录");
        var hotels = sys["HotelInfo"] as IDictionary;
        object info = hotels == null ? null : hotels[hotel];
        if (info == null) throw new InvalidOperationException("酒店资料尚未初始化");
        string hotelName = Convert.ToString(info.GetType().GetProperty("HotelName").GetValue(info, null));
        string businessDate = ((DateTime)info.GetType().GetProperty("HotelDate").GetValue(info, null)).ToString("yyyy-MM-dd");
        object server = sys["ServerLogin"];
        string address = Convert.ToString(server.GetType().GetProperty("AppServerIP").GetValue(server, null));
        if (command == "booking_context") return new { ok = true, hotel_code = hotel, hotel_name = hotelName, business_date = businessDate };
        if (command.StartsWith("booking|")) return HuiyunBookingQuery.Execute(sys, home, hotel, hotelName, address, command);
        var assembly = Assembly.LoadFrom(Path.Combine(home, "CshisNetClientWebProxy.dll"));
        var type = assembly.GetType("CshisNet.Client.Common.RoomServiceClient", true);
        var functions = Assembly.LoadFrom(Path.Combine(home, "CshisNetBasicCommon.dll"))
            .GetType("CshisNet.BasicCommon.PublicFunctions", true);
        var operators = sys["SystemOper"] as IDictionary;
        object oper = operators == null ? sys["SystemOper"] : operators[hotel];
        string buildings = Convert.ToString(oper.GetType().GetMethod("GetIniItem", new[] { typeof(string), typeof(string) })
            .Invoke(oper, new object[] { "基本参数", "可操作楼区" }));
        string allowed = String.IsNullOrWhiteSpace(buildings) ? " and (1=2)" : " and {0}.bld_cd in ('" +
            buildings.Trim().Replace("'", "''").Replace(",", "','") + "')";
        string roomCondition = "1=1" + (allowed.Contains("{0}") ? String.Format(allowed, "rmstus") : allowed);
        string blockCondition = "1=1" + (allowed.Contains("{0}") ? String.Format(allowed, "houseblock") : allowed);
        var encode = functions.GetMethod("EncodeString", new[] { typeof(string) });
        object proxy = Activator.CreateInstance(type);
        try {
            string original = (string)type.GetProperty("Url").GetValue(proxy, null);
            var manager = Assembly.LoadFrom(Path.Combine(home, "CAS.dll")).GetType("GenericFrameWork.CAS.URLManager", true);
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) })
                .Invoke(null, new object[] { original, address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, cookies, null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            var results = new Dictionary<string, object>();
            foreach (string method in new[] { "GetRoomInfoDataCompress", "GetRoomtypUsing" }) {
                Type[] signature = method == "GetRoomtypUsing" ? new[] { typeof(string) } : new[] { typeof(string), typeof(string) };
                object[] parameters = method == "GetRoomtypUsing" ? new object[] { hotel } : new object[] {
                    encode.Invoke(null, new object[] { roomCondition }), encode.Invoke(null, new object[] { blockCondition }) };
                DataSet data;
                try {
                    object response = type.GetMethod(method, signature).Invoke(proxy, parameters);
                    data = method == "GetRoomtypUsing" ? response as DataSet :
                        functions.GetMethod("UnCompressDataSet", new[] { typeof(byte[]) }).Invoke(null, new[] { response }) as DataSet;
                }
                catch (TargetInvocationException error) {
                    var cause = error.InnerException ?? error;
                    return new { ok = false, error_type = cause.GetType().Name, method,
                        message = "慧云 " + method + " 只读查询失败" };
                }
                if (data == null) throw new InvalidOperationException("房态响应为空");
                results[method] = Tables(data, command == "describe");
                if (method != "GetRoomtypUsing") Thread.Sleep(2000);
            }
            Thread.Sleep(2000);
            results["GetRoomUsedInfoObj"] = Revenue(sys, home, hotel, buildings, info, assembly, functions, manager, address);
            if (command == "describe") {
                Thread.Sleep(2000);
                results["GetRmTypeMappingInfo"] = Mapping(sys, hotel, assembly, manager, address);
                Thread.Sleep(2000);
                results["GetAccountData"] = BookingDescribe(sys, hotel, buildings, info, assembly, functions, manager, address);
            }
            if (hotel != Convert.ToString(sys["CurrentHotel"])) throw new InvalidOperationException("查询期间酒店已切换");
            return new { ok = true, hotel_code = hotel, hotel_name = hotelName, business_date = businessDate,
                captured_at = DateTimeOffset.Now.ToString("o"), reports = results };
        } finally { var disposable = proxy as IDisposable; if (disposable != null) disposable.Dispose(); }
    }

    private static object Revenue(IDictionary sys, string home, string hotel, string buildings, object info,
        Assembly assembly, Type functions, Type manager, string address) {
        if (String.IsNullOrWhiteSpace(buildings)) throw new InvalidOperationException("无可操作楼区，不能查询房费");
        string[] blds = buildings.Split(',');
        for (int i = 0; i < blds.Length; i++) blds[i] = blds[i].Trim();
        DateTime day = ((DateTime)info.GetType().GetProperty("HotelDate").GetValue(info, null)).Date;
        Type type = assembly.GetType("CshisNet.Client.Common.FrontServiceClient", true);
        object proxy = Activator.CreateInstance(type);
        try {
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                new object[] { type.GetProperty("Url").GetValue(proxy, null), address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            byte[] bytes = (byte[])type.GetMethod("GetRoomUsedInfoObj").Invoke(proxy,
                new object[] { "RoomType", day, day, blds, null, hotel, "" });
            Type resultType = functions.Assembly.GetType("CshisNet.BasicCommon.RoomUsedInfo", true);
            object result = functions.GetMethod("UnCompressObject", new[] { typeof(byte[]), typeof(Type) })
                .Invoke(null, new object[] { bytes, resultType });
            Type pub = Assembly.LoadFrom(Path.Combine(home, "CshisNetClientPub.dll"))
                .GetType("CshisNet.Client.Pub.ClientPubFunc", true);
            var data = (IDictionary)pub.GetMethod("FormatRoomUsed", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new[] { result });
            var records = new List<Dictionary<string, object>>();
            string[] fields = { "total", "inHouse", "definiteResv", "ResvAmt", "tmpAmt" };
            foreach (DictionaryEntry dateEntry in data) foreach (DictionaryEntry entry in (IDictionary)dateEntry.Value) {
                string key = Convert.ToString(entry.Key).Trim();
                if (key == "" || key == "**") continue;
                object stat = entry.Value;
                var row = new Dictionary<string, object>();
                row["room_type_id"] = key;
                row["stay_date"] = ((DateTime)dateEntry.Key).ToString("yyyy-MM-dd");
                foreach (string field in fields) row[field] = stat.GetType().GetField(field).GetValue(stat);
                records.Add(row);
            }
            return new[] { new { name = "room_revenue", count = records.Count, records } };
        } finally { ((IDisposable)proxy).Dispose(); }
    }

    private static object BookingDescribe(IDictionary sys, string hotel, string buildings, object info, Assembly assembly,
        Type functions, Type manager, string address) {
        if (String.IsNullOrWhiteSpace(buildings)) throw new InvalidOperationException("无可操作楼区");
        DateTime day = ((DateTime)info.GetType().GetProperty("HotelDate").GetValue(info, null)).Date;
        string allowed = buildings.Trim().Replace("'", "''").Replace(",", "','");
        string condition = "account.arr_dt >= '" + day.AddDays(-29).ToString("yyyy-MM-dd") +
            "' and account.arr_dt < '" + day.AddDays(1).ToString("yyyy-MM-dd") +
            "' and account.bld_cd in ('" + allowed + "')";
        var encode = functions.GetMethod("EncodeString", new[] { typeof(string) });
        Type type = assembly.GetType("CshisNet.Client.Common.FrontServiceClient", true);
        object proxy = Activator.CreateInstance(type);
        try {
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                new object[] { type.GetProperty("Url").GetValue(proxy, null), address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            byte[] bytes = (byte[])type.GetMethod("GetAccountData").Invoke(proxy,
                new object[] { "", 1, 100, encode.Invoke(null, new object[] { "account.arr_dt" }),
                    encode.Invoke(null, new object[] { condition }), hotel });
            var data = (DataSet)functions.GetMethod("UnCompressDataSet", new[] { typeof(byte[]) }).Invoke(null, new object[] { bytes });
            var tables = new List<object>();
            foreach (DataTable table in data.Tables) {
                var columns = new List<string>();
                foreach (DataColumn column in table.Columns) columns.Add(column.ColumnName);
                var sample = new Dictionary<string, object>();
                var statuses = new Dictionary<string, int>();
                var accounts = new HashSet<string>();
                var reservations = new HashSet<string>();
                var shares = new Dictionary<string, int>();
                var groups = new Dictionary<string, List<object>>();
                var reservationRooms = new HashSet<string>();
                var reservationShares = new HashSet<string>();
                int blankRooms = 0;
                int withReservation = 0;
                int defaultReservation = 0;
                var shareRelations = new Dictionary<string, int>();
                foreach (DataRow row in table.Rows) if (table.Columns.Contains("acct_stus")) {
                    string status = Convert.ToString(row["acct_stus"]);
                    statuses[status] = statuses.ContainsKey(status) ? statuses[status] + 1 : 1;
                    if (table.Columns.Contains("resv_num") && !String.IsNullOrWhiteSpace(Convert.ToString(row["resv_num"]))) withReservation++;
                    if (table.Columns.Contains("acct_num")) accounts.Add(Convert.ToString(row["acct_num"]));
                    if (table.Columns.Contains("resv_num")) reservations.Add(Convert.ToString(row["resv_num"]));
                    string reservation = Convert.ToString(row["resv_num"]);
                    string room = Convert.ToString(row["rm_num"]).Trim();
                    if (room == "") blankRooms++;
                    reservationRooms.Add(reservation + "|" + room);
                    reservationShares.Add(reservation + "|" + Convert.ToString(row["shr_num"]).Trim());
                    if (!groups.ContainsKey(reservation)) groups[reservation] = new List<object>();
                    groups[reservation].Add(new { room = row["rm_num"], type = row["rm_typ"], rooms = row["rm_nums"],
                        price = row["rt_amt"], status = row["acct_stus"], share = row["shr_flg"] });
                    if (table.Columns.Contains("resv_num") && new[] { "0", "-1", "", "00000000" }.Contains(Convert.ToString(row["resv_num"]).Trim())) defaultReservation++;
                    if (table.Columns.Contains("shr_flg")) {
                        string share = Convert.ToString(row["shr_flg"]);
                        shares[share] = shares.ContainsKey(share) ? shares[share] + 1 : 1;
                        string account = Convert.ToString(row["acct_num"]).Trim();
                        string shared = Convert.ToString(row["shr_num"]).Trim();
                        string relation = share + ":" + (shared == "" ? "empty" : shared == account ? "same" : "other");
                        shareRelations[relation] = shareRelations.ContainsKey(relation) ? shareRelations[relation] + 1 : 1;
                    }
                }
                if (table.Rows.Count > 0) foreach (string field in new[] { "arr_dt", "dpt_dt", "acct_stus", "AcctStus", "rm_typ", "rm_nums", "rt_amt" })
                    if (table.Columns.Contains(field)) sample[field] = table.Rows[0][field] == DBNull.Value ? null : table.Rows[0][field];
                tables.Add(new { name = table.TableName, count = table.Rows.Count, columns, sample, statuses, with_reservation = withReservation,
                    distinct_accounts = accounts.Count, distinct_reservations = reservations.Count, share_flags = shares,
                    share_relations = shareRelations, default_reservations = defaultReservation,
                    distinct_reservation_rooms = reservationRooms.Count, distinct_reservation_shares = reservationShares.Count, blank_rooms = blankRooms,
                    group_samples = groups.Values.Where(g => g.Count > 1).Take(3).ToArray() });
            }
            return new { start_date = day.AddDays(-29).ToString("yyyy-MM-dd"), end_date = day.ToString("yyyy-MM-dd"), tables };
        } finally { ((IDisposable)proxy).Dispose(); }
    }

    private static object Mapping(IDictionary sys, string hotel, Assembly assembly, Type manager, string address) {
        Type type = assembly.GetType("CshisNet.Client.Common.OTAService.OTAService", true);
        object proxy = Activator.CreateInstance(type);
        try {
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                new object[] { type.GetProperty("Url").GetValue(proxy, null), address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["CookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            var data = (DataSet)type.GetMethod("GetRmTypeMappingInfo").Invoke(proxy, new object[] { hotel });
            var tables = new List<object>();
            foreach (DataTable table in data.Tables) {
                var columns = new List<string>();
                foreach (DataColumn column in table.Columns) columns.Add(column.ColumnName);
                var records = new List<Dictionary<string, object>>();
                foreach (DataRow row in table.Rows) {
                    var record = new Dictionary<string, object>();
                    foreach (string field in new[] { "htl_cd", "htl_cd_pms", "rm_type_pms", "channel_cd", "htl_cd_ota", "rm_type_ota" })
                        if (table.Columns.Contains(field)) record[field] = row[field] == DBNull.Value ? null : row[field];
                    records.Add(record);
                }
                tables.Add(new { name = table.TableName, count = table.Rows.Count, columns, records });
            }
            return new { endpoint = url, tables };
        } finally { ((IDisposable)proxy).Dispose(); }
    }

    private static object Tables(DataSet data, bool describe) {
        var tables = new List<object>();
        foreach (DataTable table in data.Tables) {
            if (!describe && table.TableName != "roomstus" && table.TableName != "sysconf") continue;
            var columns = new List<string>();
            if (describe) foreach (DataColumn column in table.Columns) columns.Add(column.ColumnName);
            var records = new List<Dictionary<string, object>>();
            string[] fields = table.TableName == "roomstus" ? new[] {
                "htl_cd", "rm_num", "bld_cd", "rm_typ", "rm_stus", "stus", "cln_stus",
                "block_flg", "cnt_rm", "rm_bed", "totrm", "totmaintain", "extstus", "except_flg",
                "rm_realnum", "lock_typ", "todayresvflg" } : new[] { "para_cd", "para_drpt", "para_typ" };
            if (!describe) foreach (string field in fields) if (table.Columns.Contains(field)) columns.Add(field);
            if (!describe) foreach (DataRow row in table.Rows) {
                var record = new Dictionary<string, object>();
                foreach (string field in fields) if (table.Columns.Contains(field))
                    record[field] = row[field] == DBNull.Value ? null : row[field];
                records.Add(record);
            }
            tables.Add(new { name = table.TableName, columns, count = table.Rows.Count, records });
        }
        return tables;
    }
}

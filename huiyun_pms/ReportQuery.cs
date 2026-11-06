using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Reflection;

public static class HuiyunReportQuery {
    public static object Execute(IDictionary sys, string home, string hotel, string hotelName, string[] args) {
        bool monthly = args[3] == "monthly";
        bool roomDaily = args[3] == "room_daily";
        bool roomMonthly = args[3] == "room_monthly";
        bool range = monthly || roomMonthly;
        DateTime start = DateTime.ParseExact(args[1], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        DateTime end = DateTime.ParseExact(args[2], "yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (start > end || end.Date >= DateTime.Now.Date || (!range && start != end)
                || (roomMonthly && (end - start).TotalDays > 29))
            throw new InvalidOperationException("经营报表查询范围必须是已完成营业日");
        object oper = ((IDictionary)sys["SystemOper"])[hotel];
        string buildings = Convert.ToString(oper.GetType().GetMethod("GetIniItem", new[] { typeof(string), typeof(string) })
            .Invoke(oper, new object[] { "基本参数", "可操作楼区" })).Trim();
        if (String.IsNullOrWhiteSpace(buildings)) throw new InvalidOperationException("无可操作楼区");
        var assembly = Assembly.LoadFrom(Path.Combine(home, "CshisNetClientWebProxy.dll"));
        Type type = assembly.GetType("CshisNet.Client.Common.ReportServiceClient", true);
        var manager = Assembly.LoadFrom(Path.Combine(home, "CAS.dll")).GetType("GenericFrameWork.CAS.URLManager", true);
        object proxy = Activator.CreateInstance(type);
        try {
            object server = sys["ServerLogin"];
            string address = Convert.ToString(server.GetType().GetProperty("ReportServerIP").GetValue(server, null));
            string url = (string)manager.GetMethod("chgIpInURL", new[] { typeof(string), typeof(string) }).Invoke(null,
                new object[] { type.GetProperty("Url").GetValue(proxy, null), address });
            type.GetProperty("Url").SetValue(proxy, url, null);
            type.GetProperty("CookieContainer").SetValue(proxy, ((IDictionary)sys["RptCookieContainer"])[hotel], null);
            type.GetProperty("Timeout").SetValue(proxy, 20000, null);
            // 报表服务要求“原值[显示说明]”，裸值会返回无数据模板。
            string[] hotelParameter = new[] { "酒店代码", hotel + "[" + hotelName + "]" };
            string[] buildingParameter = new[] { roomDaily || roomMonthly ? "楼号" : "楼区", buildings + "[可操作楼区]" };
            string[][] parameters = range ? new[] { hotelParameter, buildingParameter,
                new[] { "起始日期", args[1] + "[" + args[1] + "]" }, new[] { "终止日期", args[2] + "[" + args[2] + "]" } } :
                new[] { hotelParameter, buildingParameter, new[] { "统计日期", args[1] + "[" + args[1] + "]" } };
            string report = monthly ? "CssEb03" : roomMonthly ? "Csssc08b" : roomDaily ? "Csssc08a" : "csseb01b";
            string name = monthly ? "酒店营业月报" : roomMonthly ? "房类分析报告月报（权责发生）" :
                roomDaily ? "房类分析报告日报（权责发生）" : "营业状况报表";
            byte[] bytes = (byte[])type.GetMethod("GetReportData").Invoke(proxy,
                new object[] { report, name,
                    parameters, "", "excelrecord", hotel });
            if (bytes == null || bytes.Length == 0) throw new InvalidOperationException("经营报表响应为空");
            if (hotel != Convert.ToString(sys["CurrentHotel"])) throw new InvalidOperationException("查询期间酒店已切换");
            return new { ok = true, hotel_code = hotel, hotel_name = hotelName,
                business_date = args[1], start_date = args[1], end_date = args[2], report_base64 = Convert.ToBase64String(bytes) };
        } finally { ((IDisposable)proxy).Dispose(); }
    }
}

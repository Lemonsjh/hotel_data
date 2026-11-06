using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

public static class HuiyunSessionQuery {
    public static object Execute(IDictionary sys, string command, Func<object> read, Func<string, bool> refresh = null) {
        string hotel = Convert.ToString(sys["CurrentHotel"]);
        bool business = command != "booking_context" && !command.EndsWith("|daily") && !command.EndsWith("|monthly")
            && !command.EndsWith("|room_daily") && !command.EndsWith("|room_monthly");
        for (int attempt = 0; ; attempt++) {
            if (hotel != Convert.ToString(sys["CurrentHotel"]))
                return new { ok = false, error_type = "InvalidOperationException", error_code = "hotel_changed",
                    message = "慧云查询期间酒店已切换，停止本次采集" };
            try { return read(); }
            catch (Exception error) {
                string method = Convert.ToString(error.Data["huiyun_method"]);
                Exception cause = error;
                while (cause is TargetInvocationException && cause.InnerException != null) cause = cause.InnerException;
                bool invalidSession = cause.GetType().Name == "SoapException" && cause.Message.Contains("System Oper Error");
                if (attempt == 0 && business && invalidSession && hotel != "") {
                    try { if ((refresh ?? Refresh)(hotel)) continue; }
                    catch { /* 恢复失败后保留原查询错误，不继续重试。 */ }
                }
                string label = "慧云" + (method == "" ? "" : " " + method);
                return new { ok = false, error_type = cause.GetType().Name,
                    error_code = invalidSession ? "session_invalid" : "query_failed", method,
                    message = invalidSession ? label + "服务端会话失效（System Oper Error），请关闭采集客户端后从配置页重新打开并登录" :
                        label + "只读查询失败，请检查客户端或服务端状态" };
            }
        }
    }

    private static bool Refresh(string hotel) {
        object root = null;
        foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies()) {
            if (assembly.GetName().Name != "System.Windows.Forms") continue;
            foreach (object form in (IEnumerable)assembly.GetType("System.Windows.Forms.Application").GetProperty("OpenForms").GetValue(null, null)) {
                if (form.GetType().FullName != "GenericFrameWork.MainShellApp.MainShellForm") continue;
                object work = form.GetType().GetField("workItem", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance).GetValue(form);
                root = work.GetType().GetProperty("RootWorkItem").GetValue(work, null);
            }
        }
        if (root == null) return false;
        var queue = new Queue<object>(); queue.Enqueue(root);
        for (int i = 0; queue.Count > 0 && i < 100; i++) {
            object work = queue.Dequeue(); MethodInfo refresh = null; FieldInfo code = null;
            for (Type type = work.GetType(); type != null; type = type.BaseType) {
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly;
                if (refresh == null) refresh = type.GetMethod("ReloginAppService", flags);
                if (code == null) code = type.GetField("OperateHotelCd", flags);
            }
            if (refresh != null && code != null && Convert.ToString(code.GetValue(work)) == hotel) {
                refresh.Invoke(work, new object[] { null }); return true;
            }
            var children = work.GetType().GetProperty("WorkItems");
            if (children == null) continue;
            foreach (object item in (IEnumerable)children.GetValue(work, null)) {
                var value = item.GetType().GetProperty("Value");
                queue.Enqueue(value == null ? item : value.GetValue(item, null));
            }
        }
        return false;
    }
}

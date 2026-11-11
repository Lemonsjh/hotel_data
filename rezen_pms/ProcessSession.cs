using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using Microsoft.Diagnostics.Runtime;

class ProcessSession {
    static ClrHeap heap;

    static int Main() {
        try {
            foreach (Process process in Process.GetProcessesByName("RezenOne")) {
                string token = Read(process.Id);
                if (Accept(token)) {
                    Console.OutputEncoding = new ASCIIEncoding();
                    Console.Out.Write(token);
                    return 0;
                }
            }
        } catch {
        }
        Console.Error.WriteLine("rezen session not found");
        return 1;
    }

    static bool Accept(string token) {
        if (string.IsNullOrEmpty(token) || token.Length < 8 || token.Length > 256) return false;
        foreach (char c in token)
            if (c <= ' ' || c == ';' || c == '"' || c == '\\') return false;
        return true;
    }

    static string Read(int pid) {
        try {
            using (DataTarget target = DataTarget.AttachToProcess(pid, 8000, AttachFlag.Passive)) {
                if (target.ClrVersions.Count == 0) return "";
                ClrRuntime runtime = target.ClrVersions[0].CreateRuntime();
                heap = runtime.Heap;
                string hotel = CurrentHotel();
                Dictionary<string, string> sessions = HotelSessions(runtime);
                if (hotel.Length > 0 && sessions.ContainsKey(hotel)) return sessions[hotel];
                if (hotel.Length == 0 && sessions.Count == 1) {
                    foreach (string value in sessions.Values) return value;
                }
                return "";
            }
        } catch {
            return "";
        }
    }

    static string CurrentHotel() {
        string found = "";
        foreach (ulong work in WorkItems()) {
            string hotel = SysString(work, "CurrentHotel").Trim();
            if (hotel.Length == 0) continue;
            if (found.Length == 0) found = hotel;
            else if (found != hotel) return "";
        }
        return found;
    }

    static List<ulong> WorkItems() {
        var found = new List<ulong>();
        foreach (ClrObject obj in heap.EnumerateObjects()) {
            if (obj.Type != null && obj.Type.Name == "Rezen.One.MainWorkItem") found.Add(obj.Address);
        }
        return found;
    }

    static string SysString(ulong work, string key) {
        ulong list = Ref(Ref(work, "state"), "_entriesArray");
        int size = IntOf(list, "_size");
        ulong items = Ref(list, "_items");
        ClrType array = heap.GetObjectType(items);
        if (array == null || !array.IsArray || size < 0) return "";
        int count = array.GetArrayLength(items);
        if (size > count) size = count;
        for (int i = 0; i < size; i++) {
            ulong entry = AsRef(array.GetArrayElementValue(items, i));
            if (entry == 0 || StringField(entry, "Key") != "SysObject") continue;
            return DictString(Ref(entry, "Value"), key);
        }
        return "";
    }

    static string DictString(ulong addr, string wanted) {
        ClrType type = heap.GetObjectType(addr);
        if (type == null) return "";
        int count = IntOf(addr, "count");
        ulong entries = Ref(addr, "entries");
        ClrType array = heap.GetObjectType(entries);
        if (array == null || !array.IsArray || array.ComponentType == null || count < 0) return "";
        ClrType entry = array.ComponentType;
        int length = array.GetArrayLength(entries);
        int shown = 0;
        for (int i = 0; i < length && shown < count; i++) {
            ulong element = array.GetArrayElementAddress(entries, i);
            object hash = Value(entry, element, "hashCode", true);
            if (!(hash is int) || (int)hash < 0) continue;
            shown++;
            if (AsString(Value(entry, element, "key", true)) != wanted) continue;
            return AsString(Value(entry, element, "value", true));
        }
        return "";
    }

    static Dictionary<string, string> HotelSessions(ClrRuntime runtime) {
        var found = new Dictionary<string, string>();
        ClrType type = heap.GetTypeByName("Rezen.Common.ClientLoginCacheService");
        if (type == null) return found;
        ClrStaticField field = type.GetStaticFieldByName("instance");
        if (field == null) return found;
        ulong service = 0;
        foreach (ClrAppDomain domain in runtime.AppDomains) {
            try { service = AsRef(field.GetValue(domain)); } catch { service = 0; }
            if (service != 0) break;
        }
        if (service == 0) return found;
        ulong buckets = Ref(Ref(Ref(service, "hotelCookieContainers"), "m_tables"), "m_buckets");
        ClrType array = heap.GetObjectType(buckets);
        if (array == null || !array.IsArray) return found;
        int length = array.GetArrayLength(buckets);
        for (int i = 0; i < length; i++) {
            ulong node = AsRef(array.GetArrayElementValue(buckets, i));
            for (int guard = 0; node != 0 && guard < 64; guard++) {
                ClrType nodeType = heap.GetObjectType(node);
                if (nodeType == null || nodeType.GetFieldByName("m_key") == null) break;
                string key = AsString(nodeType.GetFieldByName("m_key").GetValue(node)).Trim();
                string session = SessionOf(AsRef(nodeType.GetFieldByName("m_value").GetValue(node)));
                if (key.Length > 0 && Accept(session) && !found.ContainsKey(key)) found[key] = session;
                ClrInstanceField next = nodeType.GetFieldByName("m_next");
                node = next == null ? 0 : AsRef(next.GetValue(node));
            }
        }
        return found;
    }

    static string SessionOf(ulong container) {
        if (container == 0) return "";
        ClrType containerType = heap.GetObjectType(container);
        if (containerType == null || containerType.Name != "System.Net.CookieContainer") return "";
        ulong buckets = Ref(Ref(container, "m_domainTable"), "buckets");
        ClrType array = heap.GetObjectType(buckets);
        if (array == null || !array.IsArray || array.ComponentType == null) return "";
        ClrType bucket = array.ComponentType;
        string found = "";
        int length = array.GetArrayLength(buckets);
        for (int i = 0; i < length; i++) {
            ulong element = array.GetArrayElementAddress(buckets, i);
            ulong path = AsRef(Value(bucket, element, "val", true));
            if (path == 0) continue;
            ulong sorted = Sorted(path);
            int size = IntOf(sorted, "_size");
            ulong values = Ref(sorted, "values");
            ClrType valuesType = heap.GetObjectType(values);
            if (valuesType == null || !valuesType.IsArray || size < 0) continue;
            if (size > valuesType.GetArrayLength(values)) size = valuesType.GetArrayLength(values);
            for (int j = 0; j < size; j++) {
                string session = CollectionSession(AsRef(valuesType.GetArrayElementValue(values, j)));
                if (session.Length == 0) continue;
                if (found.Length == 0) found = session;
                else if (found != session) return "";
            }
        }
        return found;
    }

    static ulong Sorted(ulong path) {
        ulong list = Ref(path, "m_list");
        ClrType type = heap.GetObjectType(list);
        if (type != null && type.Name.IndexOf("SyncSortedList", StringComparison.Ordinal) >= 0)
            return Ref(list, "_list");
        return list;
    }

    static string CollectionSession(ulong collection) {
        if (collection == 0) return "";
        ClrType type = heap.GetObjectType(collection);
        if (type == null || type.Name != "System.Net.CookieCollection") return "";
        ulong list = Ref(collection, "m_list");
        int size = IntOf(list, "_size");
        ulong items = Ref(list, "_items");
        ClrType array = heap.GetObjectType(items);
        if (array == null || !array.IsArray || size < 0) return "";
        if (size > array.GetArrayLength(items)) size = array.GetArrayLength(items);
        string found = "";
        for (int i = 0; i < size; i++) {
            ulong cookie = AsRef(array.GetArrayElementValue(items, i));
            ClrType cookieType = cookie == 0 ? null : heap.GetObjectType(cookie);
            if (cookieType == null || cookieType.Name != "System.Net.Cookie") continue;
            if (StringField(cookie, "m_name") != "ASP.NET_SessionId") continue;
            string value = StringField(cookie, "m_value");
            if (!Accept(value)) continue;
            if (found.Length == 0) found = value;
            else if (found != value) return "";
        }
        return found;
    }

    static object Value(ClrType type, ulong addr, string name, bool interior) {
        if (type == null) return null;
        ClrInstanceField field = type.GetFieldByName(name);
        if (field == null) return null;
        try { return field.GetValue(addr, interior); } catch { return null; }
    }

    static int IntOf(ulong addr, string name) {
        if (addr == 0) return -1;
        ClrType type = heap.GetObjectType(addr);
        object value = Value(type, addr, name, false);
        if (value is int) return (int)value;
        return -1;
    }

    static string StringField(ulong addr, string name) {
        if (addr == 0) return "";
        return AsString(Value(heap.GetObjectType(addr), addr, name, false));
    }

    static string AsString(object value) {
        string text = value as string;
        if (text != null) return text;
        ulong addr = AsRef(value);
        if (addr == 0) return "";
        ClrType type = heap.GetObjectType(addr);
        if (type == null || !type.IsString) return "";
        return type.GetValue(addr) as string ?? "";
    }

    static ulong Ref(ulong addr, string name) {
        if (addr == 0) return 0;
        return AsRef(Value(heap.GetObjectType(addr), addr, name, false));
    }

    static ulong AsRef(object value) {
        if (value == null || value is string || value is bool) return 0;
        if (value is int) return unchecked((uint)(int)value);
        if (value is uint) return (uint)value;
        if (value is long) {
            long number = (long)value;
            return number <= 0 ? 0 : (ulong)number;
        }
        if (value is ulong) return (ulong)value;
        return 0;
    }
}

using System;
using System.Collections;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public static class ClientHost {
    [STAThread]
    public static void Main(string[] args) {
        if (args.Length != 4) return;
        var setup = new AppDomainSetup { ApplicationBase = args[0],
            ConfigurationFile = Path.Combine(args[0], "MainShellApp.exe.config") };
        var domain = AppDomain.CreateDomain("HuiyunCollector", null, setup);
        var host = (SessionHost)domain.CreateInstanceFromAndUnwrap(
            Assembly.GetExecutingAssembly().Location, typeof(SessionHost).FullName);
        host.Run(args[0], args[1], args[2], args[3]);
    }
}

public sealed class SessionHost : MarshalByRefObject {
    private string home, stateFile, pipeName, queryFile;
    private volatile IDictionary session;
    private bool listening;
    private System.Windows.Forms.Timer timer;
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    public override object InitializeLifetimeService() { return null; }

    private Assembly Resolve(object sender, ResolveEventArgs args) {
        string name = new AssemblyName(args.Name).Name + ".dll";
        foreach (string folder in new[] { home, Path.Combine(Path.GetDirectoryName(home), "System") }) {
            string file = Path.Combine(folder, name);
            if (File.Exists(file)) return Assembly.LoadFrom(file);
        }
        return null;
    }

    private void State(string status) {
        var json = new JavaScriptSerializer().Serialize(new {
            status, pid = System.Diagnostics.Process.GetCurrentProcess().Id, pipe = pipeName,
            client_home = home, updated_at = DateTime.Now.ToString("s") });
        string temporary = stateFile + ".tmp";
        File.WriteAllText(temporary, json, Encoding.UTF8);
        if (File.Exists(stateFile)) File.Replace(temporary, stateFile, null);
        else File.Move(temporary, stateFile);
    }

    public void Run(string clientHome, string state, string pipe, string query) {
        home = clientHome; stateFile = state; pipeName = pipe; queryFile = query;
        AppDomain.CurrentDomain.AssemblyResolve += Resolve;
        Directory.SetCurrentDirectory(home);
        State("waiting_for_login");
        timer = new System.Windows.Forms.Timer { Interval = 2000 };
        timer.Tick += FindSession;
        EventHandler start = null;
        start = (sender, args) => { Application.Idle -= start; timer.Start(); };
        Application.Idle += start;
        try {
            Assembly.LoadFrom(Path.Combine(home, "MainShellApp.exe")).EntryPoint.Invoke(
                null, new object[] { new string[0] });
        } finally { timer.Stop(); State("closed"); }
    }

    private void FindSession(object sender, EventArgs args) {
        try {
            foreach (Form form in Application.OpenForms) {
                if (form.GetType().FullName != "GenericFrameWork.MainShellApp.MainShellForm") continue;
                var field = form.GetType().GetField("workItem", Fields);
                object work = field == null ? null : field.GetValue(form);
                if (work == null) continue;
                object root = work.GetType().GetProperty("RootWorkItem").GetValue(work, null);
                object state = root.GetType().GetProperty("State").GetValue(root, null);
                var sys = state.GetType().GetProperty("Item").GetValue(state, new object[] { "SysObject" }) as IDictionary;
                if (sys == null || !sys.Contains("CookieContainer")) continue;
                session = sys;
                if (!listening) {
                    State("ready");
                    listening = true;
                    new Thread(Listen) { IsBackground = true }.Start();
                }
                return;
            }
        } catch { /* 登录界面尚未完成初始化，下一次仅检查本地对象。 */ }
    }

    private void Listen() {
        var security = new PipeSecurity();
        security.SetAccessRuleProtection(true, false);
        security.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
            PipeAccessRights.FullControl, AccessControlType.Allow));
        while (true) {
            using (var pipe = new NamedPipeServerStream(pipeName, PipeDirection.InOut, 1,
                PipeTransmissionMode.Message, PipeOptions.None, 65536, 65536, security)) {
                pipe.WaitForConnection();
                try {
                    var buffer = new byte[128];
                    int count = pipe.Read(buffer, 0, buffer.Length);
                    if (!pipe.IsMessageComplete) throw new InvalidOperationException("请求过长");
                    string command = Encoding.UTF8.GetString(buffer, 0, count);
                    object result;
                    if (command == "status") result = new { ok = true, status = "ready" };
                    else if (command == "describe" || command == "room_status" || command == "booking_context" || command.StartsWith("booking|")) {
                        // 字节加载避免锁住查询组件，开发升级无需反复登录。
                        var query = Assembly.Load(File.ReadAllBytes(queryFile));
                        result = query.GetType("HuiyunRoomQuery", true).GetMethod("Execute")
                            .Invoke(null, new object[] { session, home, command });
                    } else throw new InvalidOperationException("不支持的只读请求");
                    Reply(pipe, result);
                } catch (Exception error) {
                    while (error is TargetInvocationException && error.InnerException != null) error = error.InnerException;
                    Reply(pipe, new { ok = false, error_type = error.GetType().Name,
                        message = "慧云只读查询失败，请确认客户端仍已登录" });
                }
            }
        }
    }

    private static void Reply(Stream pipe, object result) {
        var serializer = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        byte[] bytes = Encoding.UTF8.GetBytes(serializer.Serialize(result));
        try { pipe.Write(bytes, 0, bytes.Length); pipe.Flush(); }
        catch (IOException) { /* 采集进程取消时保持酒店客户端运行。 */ }
    }
}

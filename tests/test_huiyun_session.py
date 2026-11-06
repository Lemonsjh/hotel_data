"""Run the native recovery policy with fake delegates; never open a PMS client."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
HARNESS = r'''
using System;
using System.Collections;
using System.Net;
using System.Reflection;
using System.Web.Services.Protocols;

public static class Checks {
    private static object Get(object result, string name) {
        return result.GetType().GetProperty(name).GetValue(result, null);
    }
    private static void Assert(bool value, string message) {
        if (!value) throw new Exception(message);
    }
    public static void Main(string[] args) {
        string scenario = args[0];
        var sys = new Hashtable { { "CurrentHotel", "fake-hotel" } };
        int reads = 0, refreshes = 0;
        bool success = scenario == "normal" || scenario == "recover" || scenario == "revenue";
        string command = scenario == "report" ? "booking|2026-11-05|2026-11-05|daily" :
            scenario == "booking" ? "booking|2026-11-06|2026-11-06" :
            scenario == "revenue" ? "booking|2026-11-05|2026-11-05|revenue" : "room_status";
        object result = HuiyunSessionQuery.Execute(sys, command, () => {
            reads++;
            if (scenario == "normal" || (reads == 2 && success)) return new { ok = true };
            Exception fault = scenario == "network" ? (Exception)new WebException("TLS closed secret-cookie") :
                new SoapException(scenario == "other_soap" ? "Other error secret-cookie" : "System Oper Error secret-cookie",
                                  SoapException.ServerFaultCode);
            var error = new TargetInvocationException(new TargetInvocationException(fault));
            error.Data["huiyun_method"] = "GetRoomInfoDataCompress";
            throw error;
        }, hotel => {
            refreshes++;
            Assert(hotel == "fake-hotel", "wrong hotel recovery");
            if (scenario == "changed_hotel") sys["CurrentHotel"] = "other-hotel";
            if (scenario == "refresh_exception") throw new Exception("secret-cookie");
            return scenario != "refresh_false";
        });
        Assert((bool)Get(result, "ok") == success, "unexpected result");
        bool skipsRefresh = scenario == "normal" || scenario == "network" || scenario == "other_soap" || scenario == "report";
        Assert(refreshes == (skipsRefresh ? 0 : 1), "refresh must occur at most once");
        bool retries = !skipsRefresh && scenario != "changed_hotel" && scenario != "refresh_false" && scenario != "refresh_exception";
        Assert(reads == (retries ? 2 : 1), "wrong number of queries");
        if (!success) {
            string code = Convert.ToString(Get(result, "error_code"));
            Assert(code == (scenario == "changed_hotel" ? "hotel_changed" :
                scenario == "network" || scenario == "other_soap" ? "query_failed" : "session_invalid"), "wrong diagnostic");
            string message = Convert.ToString(Get(result, "message"));
            Assert(!message.Contains("secret-cookie"), "raw fault leaked");
        }
        Console.WriteLine(scenario + " passed");
    }
}
'''


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Requires Windows .NET Framework compiler')
class HuiyunNativeSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = ROOT / 'exports'
        output.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='huiyun-session-test-', dir=output)
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        harness = folder / 'Checks.cs'
        harness.write_text(HARNESS, encoding='utf-8')
        cls.program = folder / 'Checks.exe'
        subprocess.run([str(CSC), '/nologo', '/reference:System.Web.Services.dll',
                        f'/out:{cls.program}', str(ROOT / 'huiyun_pms/SessionQuery.cs'), str(harness)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def test_recovery_is_bounded_and_scoped_to_business_session_faults(self):
        for scenario in ('normal', 'recover', 'booking', 'revenue', 'persistent', 'network', 'other_soap',
                         'report', 'refresh_false', 'refresh_exception', 'changed_hotel'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.program), scenario], capture_output=True, timeout=10,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()

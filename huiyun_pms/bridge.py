"""启动原慧云客户端，并通过当前用户专用命名管道执行白名单只读查询。"""
import ctypes
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get('LOCALAPPDATA', str(ROOT.parent / 'exports'))) / 'HotelAgent' / 'huiyun'
STATE = RUNTIME / 'state_v2.json'  # 新增预订查询命令，旧启动器不会覆盖新会话状态。
DEFAULT_CLIENT = 'C:/CshisNetProgram/Main/MainShellApp.exe'


def state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def request(command, timeout=70):
    if os.name != 'nt':
        raise RuntimeError('慧云客户端采集仅支持 Windows')
    if command not in {'status', 'describe', 'room_status', 'booking_context'} and not re.fullmatch(r'booking\|\d{4}-\d{2}-\d{2}\|\d{4}-\d{2}-\d{2}(?:\|(?:guests|daily|monthly|revenue|room_daily|room_monthly))?', command):
        raise ValueError('不支持的慧云只读请求')
    current = state()
    pipe = current.get('pipe', '')
    if current.get('status') != 'ready' or not pipe.startswith('hotel-agent-huiyun-'):
        raise RuntimeError('请先从配置页打开慧云客户端并手动登录')
    if command != 'status':
        compile_query()
    address = '\\\\.\\pipe\\' + pipe
    wait = ctypes.windll.kernel32.WaitNamedPipeW
    wait.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    wait.restype = ctypes.c_bool
    if not wait(address, 2000):
        raise RuntimeError('慧云采集会话未就绪或正在被使用，请稍后再试')
    from multiprocessing.connection import Client
    with Client(address, family='AF_PIPE') as connection:
        connection.send_bytes(command.encode())
        if not connection.poll(timeout):
            raise RuntimeError('慧云查询超时')
        result = json.loads(connection.recv_bytes(8 * 1024 * 1024).decode('utf-8'))
    if not isinstance(result, dict):
        raise RuntimeError('慧云响应无效')
    if result.get('ok') is not True:
        raise RuntimeError(f"{result.get('message', '慧云响应无效')} ({result.get('error_type', 'unknown')})")
    return result


def compile_query():
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise RuntimeError('缺少 .NET Framework 4 编译器，请确认慧云运行环境已安装')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    common = [str(compiler), '/nologo', '/platform:x86', '/reference:System.Data.dll']
    query = RUNTIME / 'RoomQuery.dll'
    sources = [ROOT / name for name in ('RoomQuery.cs', 'BookingQuery.cs', 'GuestQuery.cs', 'ReportQuery.cs', 'RevenueQuery.cs')]
    digest = hashlib.sha256(b''.join(source.read_bytes() for source in sources)).hexdigest()
    marker = RUNTIME / 'query.sha256'
    if not query.exists() or not marker.exists() or marker.read_text() != digest:
        subprocess.run([*common, '/target:library', f'/out:{query}', *map(str, sources)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        marker.write_text(digest)
    return compiler, query


def start(client_path=DEFAULT_CLIENT):
    client = Path(client_path).resolve()
    if not client.is_file() or client.name.lower() != 'mainshellapp.exe':
        raise RuntimeError('慧云客户端路径无效，请选择 MainShellApp.exe')
    compiler, query = compile_query()
    try:
        request('status', 2)
        if Path(state().get('client_home', '')).resolve() != client.parent:
            raise ValueError('请先关闭当前采集客户端，再切换慧云安装路径')
        return state()  # 保留已登录的会话，不重复打开窗口。
    except (OSError, RuntimeError):
        pass
    if state().get('status') == 'waiting_for_login' and process_alive(state().get('pid')):
        raise RuntimeError('慧云登录窗口已打开，请先完成手动登录')
    digest = hashlib.sha256((ROOT / 'ClientHost.cs').read_bytes()).hexdigest()[:10]
    host = RUNTIME / f'HuiyunCollector_{digest}.exe'
    if not host.exists():
        subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x86',
                        f'/out:{host}', '/reference:System.Windows.Forms.dll', '/reference:System.Data.dll',
                        '/reference:System.Web.Extensions.dll', str(ROOT / 'ClientHost.cs')],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    for name in ['PMSCommon.dll', 'PMSUI.dll', 'ProfileCatalog.xml']:
        source, destination = client.parent / name, RUNTIME / name
        if not destination.exists() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
            shutil.copy2(source, destination)
    shutil.copy2(client.with_suffix('.exe.config'), host.with_suffix('.exe.config'))
    pipe = 'hotel-agent-huiyun-' + uuid.uuid4().hex
    process = subprocess.Popen([str(host), str(client.parent), str(STATE), pipe, str(query)], cwd=RUNTIME)
    return {'status': 'starting', 'pid': process.pid}


def process_alive(pid):
    if os.name != 'nt' or not isinstance(pid, int):
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)

"""只读定位本机慧云客户端，不启动程序或修改配置。"""
import ctypes
import os
from pathlib import Path
import subprocess

from huiyun_pms import bridge

CLIENT_NAME = 'MainShellApp.exe'


def running_clients():
    if os.name != 'nt':
        return []
    command = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-CimInstance Win32_Process -Filter \"Name='MainShellApp.exe'\" -ErrorAction SilentlyContinue | "
               "ForEach-Object { $_.ExecutablePath }")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def common_clients():
    if os.name != 'nt':
        return
    kernel = ctypes.windll.kernel32
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    mask = kernel.GetLogicalDrives()
    for index in range(26):
        root = Path(f'{chr(65 + index)}:/')
        if mask & (1 << index) and kernel.GetDriveTypeW(str(root)) == 3:
            yield root / 'CshisNetProgram/Main' / CLIENT_NAME
    for key in ('ProgramFiles', 'ProgramFiles(x86)'):
        if os.environ.get(key):
            yield Path(os.environ[key]) / 'CshisNetProgram/Main' / CLIENT_NAME


def valid_client(value):
    if not value:
        return None
    path = Path(os.path.expandvars(str(value).strip().strip('"')))
    try:
        if (path.name.lower() == CLIENT_NAME.lower() and path.is_file()
                and all((path.parent / name).is_file() for name in ('PMSCommon.dll', 'CshisNetClientWebProxy.dll'))):
            return str(path.resolve())
    except OSError:
        pass
    return None


def find_client(configured=''):
    session = bridge.state()
    if session.get('client_home') and bridge.process_alive(session.get('pid')):
        found = valid_client(Path(session['client_home']) / CLIENT_NAME)
        if found:
            return found
    for candidate in running_clients():
        found = valid_client(candidate)
        if found:
            return found
    for candidate in (configured, bridge.DEFAULT_CLIENT, *common_clients()):
        found = valid_client(candidate)
        if found:
            return found
    return None

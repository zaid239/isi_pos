"""
Windows Shortcuts Utility for Noventra POS.
Creates Desktop and Startup folder shortcuts using PowerShell WScript.Shell COM API.
"""
import os
import sys
import subprocess
from typing import Tuple


def get_target_exe_path() -> str:
    """Returns the absolute path to the current running executable or main script."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    # If running in development / python mode:
    return os.path.abspath(sys.executable)


def create_windows_shortcut(target_exe: str, shortcut_path: str, name: str = "Noventra POS") -> bool:
    """Creates a Windows .lnk shortcut targeting target_exe."""
    target_dir = os.path.dirname(target_exe)
    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{shortcut_path}')
$s.TargetPath = '{target_exe}'
$s.WorkingDirectory = '{target_dir}'
$s.Description = '{name}'
$s.Save()
"""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return res.returncode == 0
    except Exception:
        return False


def setup_all_shortcuts(target_exe: str = None) -> Tuple[bool, str]:
    """
    Installs:
    1. Desktop icon: Desktop\\Noventra POS.lnk
    2. Windows Startup icon: Startup\\Noventra POS.lnk
    """
    exe_path = target_exe or get_target_exe_path()
    if not os.path.isfile(exe_path):
        return False, f"Target executable not found: {exe_path}"

    results = []

    # 1. Desktop Shortcut
    desktop_dir = os.path.join(os.environ.get("USERPROFILE", "C:\\"), "Desktop")
    if os.path.isdir(desktop_dir):
        desktop_lnk = os.path.join(desktop_dir, "Noventra POS.lnk")
        if create_windows_shortcut(exe_path, desktop_lnk, "Noventra POS"):
            results.append(f"Desktop shortcut created: {desktop_lnk}")

    # 2. Windows Startup Shortcut
    appdata = os.environ.get("APPDATA")
    if appdata:
        startup_dir = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(startup_dir):
            startup_lnk = os.path.join(startup_dir, "Noventra POS.lnk")
            if create_windows_shortcut(exe_path, startup_lnk, "Noventra POS"):
                results.append(f"Startup shortcut created: {startup_lnk}")

    return True, "\n".join(results)

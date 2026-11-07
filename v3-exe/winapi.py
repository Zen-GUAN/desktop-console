# -*- coding: utf-8 -*-
"""Win32 bindings for Desktop Console v3.

Mirrors v2-web/server/Native.ps1 P/Invoke declarations 1:1, plus the host-side
pieces the PowerShell version got from WinForms (tray icon, hotkeys, dialogs).
Registry access uses the stdlib winreg module instead of raw advapi32.
"""
import os
import math
import time
import threading
import ctypes
import winreg
import subprocess
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
dxva2 = ctypes.WinDLL('dxva2', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
shell32 = ctypes.WinDLL('shell32', use_last_error=True)
ole32 = ctypes.WinDLL('ole32', use_last_error=True)
comdlg32 = ctypes.WinDLL('comdlg32', use_last_error=True)

# ---------------------------------------------------------------- dxva2 / DDC-CI

class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int), ('top', ctypes.c_int),
                ('right', ctypes.c_int), ('bottom', ctypes.c_int)]

class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [('hPhysicalMonitor', ctypes.c_void_p),
                ('szPhysicalMonitorDescription', ctypes.c_wchar * 128)]

MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.POINTER(RECT), ctypes.c_void_p)

user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, MONITORENUMPROC, ctypes.c_void_p]
user32.EnumDisplayMonitors.restype = ctypes.c_bool
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = ctypes.c_bool
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.GetPhysicalMonitorsFromHMONITOR.restype = ctypes.c_bool
dxva2.GetMonitorBrightness.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                       ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
dxva2.GetMonitorBrightness.restype = ctypes.c_bool
dxva2.SetMonitorBrightness.argtypes = [ctypes.c_void_p, wintypes.DWORD]
dxva2.SetMonitorBrightness.restype = ctypes.c_bool
dxva2.DestroyPhysicalMonitor.argtypes = [ctypes.c_void_p]

def enum_monitor_handles():
    found = []
    def cb(hmon, hdc, lprect, lparam):
        found.append(hmon)
        return True
    cb_ref = MONITORENUMPROC(cb)
    if not user32.EnumDisplayMonitors(None, None, cb_ref, None):
        return []
    return found

def enum_physical_monitors():
    """[(handle, desc), ...] — mirrors Get-PhysMonitors (empty list on failure)."""
    result = []
    for hmon in enum_monitor_handles():
        count = wintypes.DWORD(0)
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
            continue
        if count.value == 0:
            continue
        buf = (PHYSICAL_MONITOR * count.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, buf):
            continue
        for i in range(count.value):
            desc = buf[i].szPhysicalMonitorDescription.replace('\x00', '').strip()
            if not desc:
                desc = 'Monitor %d' % (len(result) + 1)
            result.append((buf[i].hPhysicalMonitor, desc))
    return result

def get_brightness(handle):
    mn, cur, mx = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD(0)
    if not dxva2.GetMonitorBrightness(handle, ctypes.byref(mn), ctypes.byref(cur), ctypes.byref(mx)):
        return None
    return (mn.value, cur.value, mx.value)

def set_brightness(handle, value):
    return bool(dxva2.SetMonitorBrightness(handle, int(value) & 0xFFFFFFFF))

def destroy_monitor(handle):
    try:
        dxva2.DestroyPhysicalMonitor(handle)
    except Exception:
        pass

# ---------------------------------------------------------------- wallpaper

SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014

user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SystemParametersInfoW.restype = ctypes.c_bool

def get_wallpaper():
    buf = ctypes.create_unicode_buffer(2048)
    user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 2048, ctypes.cast(buf, ctypes.c_void_p), 0)
    return buf.value

def set_wallpaper(path):
    buf = ctypes.create_unicode_buffer(str(path))
    return bool(user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, ctypes.cast(buf, ctypes.c_void_p), 0x03))

# ---------------------------------------------------------------- broadcast

WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF

user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_wchar_p,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

def broadcast_settings(param=None):
    result = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, param, 0x0002, 1000, ctypes.byref(result))

# ---------------------------------------------------------------- registry (HKCU)

def get_reg_dword(root, path, name, default):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default

def set_reg_dword(root, path, name, value):
    v = int(value) & 0xFFFFFFFF
    with winreg.CreateKey(root, path) as key:
        try:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, v)
        except OverflowError:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, v - 0x100000000)

def rgb_to_abgr(r, g, b):
    return (0xFF000000 | ((b & 0xFF) << 16) | ((g & 0xFF) << 8) | (r & 0xFF))

def abgr_to_hex(value):
    u = int(value) & 0xFFFFFFFF
    return '#%02X%02X%02X' % (u & 255, (u >> 8) & 255, (u >> 16) & 255)

# ---------------------------------------------------------------- main window helpers

ENUMWNDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMWNDPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]

def find_main_hwnd(title):
    """Visible top-level window of this process with the given title."""
    candidates = []
    def cb(hwnd, lparam):
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            candidates.append(hwnd)
            return False
        return True
    cb_ref = ENUMWNDPROC(cb)
    user32.EnumWindows(cb_ref, 0)
    for hwnd in candidates:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            return hwnd
    return candidates[0] if candidates else 0

def begin_title_drag(hwnd):
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, 0xA1, 2, 0)   # WM_NCLBUTTONDOWN, HTCAPTION

def begin_resize(hwnd):
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, 0xA1, 17, 0)  # WM_NCLBUTTONDOWN, HTBOTTOMRIGHT

def apply_topmost(hwnd, on):
    if hwnd:
        user32.SetWindowPos(hwnd, wintypes.HWND(-1 if on else -2), 0, 0, 0, 0, 0x0013)

# ---------------------------------------------------------------- tray + hotkeys host window

WM_APP = 0x8000
WM_HOTKEY = 0x0312
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', ctypes.c_void_p), ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', ctypes.c_wchar_p), ('lpszClassName', ctypes.c_wchar_p)]

class NOTIFYICONDATAW(ctypes.Structure):
    class _UNION(ctypes.Union):
        _fields_ = [('uTimeout', wintypes.UINT), ('uVersion', wintypes.UINT)]
    _anonymous_ = ('u',)
    _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND), ('uID', wintypes.UINT),
                ('uFlags', wintypes.UINT), ('uCallbackMessage', wintypes.UINT), ('hIcon', wintypes.HICON),
                ('szTip', ctypes.c_wchar * 128), ('dwState', wintypes.DWORD), ('dwStateMask', wintypes.DWORD),
                ('szInfo', ctypes.c_wchar * 256), ('u', _UNION), ('szInfoTitle', ctypes.c_wchar * 64),
                ('dwInfoFlags', wintypes.DWORD), ('guidItem', ctypes.c_byte * 16), ('hBalloonIcon', wintypes.HICON)]

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = ctypes.c_bool
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]

class TrayHost:
    """Hidden window thread: tray icon (show/topmost/exit menu) + global hotkeys."""

    def __init__(self, title, icon_path, on_show, on_topmost, on_exit, on_hotkey,
                 on_hotkey_state=None, on_build_menu=None, on_menu=None):
        self.title = title
        self.icon_path = icon_path
        self.on_show = on_show
        self.on_topmost = on_topmost
        self.on_exit = on_exit
        self.on_hotkey = on_hotkey
        self.on_hotkey_state = on_hotkey_state or (lambda hotkey_id, ok: None)
        self.on_build_menu = on_build_menu   # fn(parent_hmenu) → 追加扩展子菜单
        self.on_menu = on_menu               # fn(cmd) → 分发扩展菜单命令
        self.topmost = False
        self.hwnd = 0
        self.hicon = 0
        self.thread = None
        self.hotkey_specs = []
        self.hotkeys_done = threading.Event()
        self._wndproc_ref = WNDPROC(self._dispatch)

    def _load_icon(self):
        if self.icon_path and os.path.exists(self.icon_path):
            hicon = user32.LoadImageW(None, self.icon_path, 1, 0, 0, 0x10 | 0x40)  # IMAGE_ICON, FROMFILE|DEFAULTSIZE
            if hicon:
                return hicon
        return user32.LoadIconW(None, 32512)  # IDI_APPLICATION

    def start(self, hotkey_specs, initial_topmost=False):
        self.topmost = bool(initial_topmost)
        self.hotkey_specs = hotkey_specs
        self.thread = threading.Thread(target=self._run, name='winctl-tray', daemon=True)
        self.thread.start()
        # 等窗口建好、热键注册完成，避免 state 接口在注册前读到全 False
        for _ in range(100):
            if self.hwnd and self.hotkeys_done.is_set():
                break
            time.sleep(0.02)

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = 'WinCtlPanel3Tray'
        if not user32.RegisterClassW(ctypes.byref(wc)):
            return
        self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, '', 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        if not self.hwnd:
            return
        self.hicon = self._load_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = 0x01 | 0x02 | 0x04  # MESSAGE | ICON | TIP
        nid.uCallbackMessage = WM_APP
        nid.hIcon = self.hicon
        nid.szTip = self.title
        self.tray_added = shell32.Shell_NotifyIconW(0, ctypes.byref(nid))  # NIM_ADD
        # global hotkeys: (id, modifiers, vk)
        for hotkey_id, mods, vk in self.hotkey_specs:
            ok = bool(user32.RegisterHotKey(self.hwnd, hotkey_id, mods, vk))
            self.on_hotkey_state(hotkey_id, ok)
        self.hotkeys_done.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def on_hotkey_state(self, hotkey_id, ok):
        pass  # overridden via attribute by app

    def _dispatch(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_HOTKEY:
                self.on_hotkey(int(wparam))
                return 0
            if msg == WM_APP:
                mouse = int(lparam) & 0xFFFF
                if mouse == WM_LBUTTONUP:
                    self.on_show()
                elif mouse == WM_RBUTTONUP:
                    self._popup_menu(hwnd)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0x00000000, 1, '显示面板')
        user32.AppendMenuW(menu, 0x00000008 if self.topmost else 0x00000000, 2, '窗口置顶')  # MF_CHECKED
        user32.AppendMenuW(menu, 0x00000800, 0, None)  # MF_SEPARATOR
        user32.AppendMenuW(menu, 0x00000000, 3, '退出（保留更改）')
        if self.on_build_menu is not None:
            try:
                self.on_build_menu(menu)
            except Exception:
                pass
        user32.SetForegroundWindow(hwnd)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0080, pt.x, pt.y, 0, hwnd, None)  # RETURNCMD|RIGHTBUTTON|NONOTIFY
        user32.DestroyMenu(menu)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        if cmd == 1:
            self.on_show()
        elif cmd == 2:
            self.topmost = not self.topmost
            self.on_topmost(self.topmost)
        elif cmd == 3:
            self.on_exit()
        elif cmd and self.on_menu is not None:
            try:
                self.on_menu(cmd)
            except Exception:
                pass

    def show_panel_window(self):
        self.on_show()

    def stop(self):
        if self.hwnd:
            for hotkey_id, _mods, _vk in getattr(self, 'hotkey_specs', []):
                user32.UnregisterHotKey(self.hwnd, hotkey_id)
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            shell32.Shell_NotifyIconW(2, ctypes.byref(nid))  # NIM_DELETE
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

# ---------------------------------------------------------------- common dialogs

class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
                ('lpstrFilter', ctypes.c_void_p), ('lpstrCustomFilter', ctypes.c_void_p),
                ('nMaxCustFilter', wintypes.DWORD), ('nFilterIndex', wintypes.DWORD),
                ('lpstrFile', ctypes.c_void_p), ('nMaxFile', wintypes.DWORD),
                ('lpstrFileTitle', ctypes.c_void_p), ('nMaxFileTitle', wintypes.DWORD),
                ('lpstrInitialDir', ctypes.c_void_p), ('lpstrTitle', ctypes.c_void_p),
                ('Flags', wintypes.DWORD), ('nFileOffset', ctypes.c_ushort), ('nFileExtension', ctypes.c_ushort),
                ('lpstrDefExt', ctypes.c_wchar_p), ('lCustData', ctypes.c_void_p), ('lpfnHook', ctypes.c_void_p),
                ('lpTemplateName', ctypes.c_void_p), ('pvReserved', ctypes.c_void_p),
                ('dwReserved', wintypes.DWORD), ('FlagsEx', wintypes.DWORD)]

comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
comdlg32.GetOpenFileNameW.restype = ctypes.c_bool

COINIT_APARTMENTTHREADED = 0x2
OFN_FILEMUSTEXIST = 0x1000
OFN_PATHMUSTEXIST = 0x0800
OFN_NOCHANGEDIR = 0x0008

def _file_dialog(owner, title, filter_text, initial_dir=None):
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        buf = ctypes.create_unicode_buffer(4096)
        fltbuf = ctypes.create_unicode_buffer(filter_text + '\0', max(1024, len(filter_text) + 4))
        titlebuf = ctypes.create_unicode_buffer(title)
        dirbuf = ctypes.create_unicode_buffer(initial_dir) if initial_dir else None
        ofn = OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
        ofn.hwndOwner = owner or None
        ofn.lpstrFilter = ctypes.cast(fltbuf, ctypes.c_void_p)
        ofn.lpstrFile = ctypes.cast(buf, ctypes.c_void_p)
        ofn.nMaxFile = 4096
        ofn.lpstrTitle = ctypes.cast(titlebuf, ctypes.c_void_p)
        if dirbuf is not None:
            ofn.lpstrInitialDir = ctypes.cast(dirbuf, ctypes.c_void_p)
        ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
        if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return buf.value
        return ''
    finally:
        ole32.CoUninitialize()

def open_image_dialog(owner, initial_dir=None):
    flt = '图片 (*.jpg;*.jpeg;*.png;*.bmp)\0*.jpg;*.jpeg;*.png;*.bmp\0'
    return _file_dialog(owner, '选择桌面壁纸', flt, initial_dir)

def open_theme_dialog(owner):
    flt = '主题文件 (*.theme)\0*.theme\0'
    themes_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Themes')
    return _file_dialog(owner, '选择主题文件', flt, themes_dir if os.path.isdir(themes_dir) else None)

class BROWSEINFOW(ctypes.Structure):
    _fields_ = [('hwndOwner', wintypes.HWND), ('pidlRoot', ctypes.c_void_p),
                ('pszDisplayName', ctypes.c_void_p), ('lpszTitle', ctypes.c_void_p),
                ('ulFlags', wintypes.UINT), ('lpfn', ctypes.c_void_p), ('lParam', ctypes.c_void_p),
                ('iImage', ctypes.c_int)]

shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
shell32.SHGetPathFromIDListW.restype = ctypes.c_bool

BIF_RETURNONLYFSDIRS = 0x0001
BIF_NEWDIALOGSTYLE = 0x0040

def browse_folder_dialog(owner, title):
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        disp = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFOW()
        bi.hwndOwner = owner or None
        bi.pszDisplayName = ctypes.cast(disp, ctypes.c_void_p)
        bi.lpszTitle = ctypes.cast(ctypes.create_unicode_buffer(title), ctypes.c_void_p)
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return ''
        pathbuf = ctypes.create_unicode_buffer(260)
        ok = shell32.SHGetPathFromIDListW(pidl, pathbuf)
        ole32.CoTaskMemFree(pidl)
        return pathbuf.value if ok else ''
    finally:
        ole32.CoUninitialize()

# ---------------------------------------------------------------- elevated helper (lockscreen)

class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG), ('hwnd', wintypes.HWND),
                ('lpVerb', ctypes.c_wchar_p), ('lpFile', ctypes.c_wchar_p), ('lpParameters', ctypes.c_wchar_p),
                ('lpDirectory', ctypes.c_wchar_p), ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                ('lpIDList', ctypes.c_void_p), ('lpClass', ctypes.c_wchar_p), ('hkeyClass', ctypes.c_void_p),
                ('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]

shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = ctypes.c_bool

SEE_MASK_NOCLOSEPROCESS = 0x40
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF

def run_elevated_powershell(parameters, timeout_s=60):
    """ShellExecute 'runas' powershell; returns exit code, -1 on UAC decline/timeout."""
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = 'runas'
    sei.lpFile = 'powershell.exe'
    sei.lpParameters = parameters
    sei.nShow = 0  # SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return -1
    if not sei.hProcess:
        return -1
    waited = kernel32.WaitForSingleObject(sei.hProcess, int(timeout_s * 1000))
    code = wintypes.DWORD(0xFFFFFFFF)
    kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(sei.hProcess)
    if waited != WAIT_OBJECT_0:
        return -1
    return code.value

# ---------------------------------------------------------------- single instance / explorer

ERROR_ALREADY_EXISTS = 183

def acquire_single_instance(mutex_name):
    kernel32.CreateMutexW(None, False, mutex_name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

def restart_explorer():
    subprocess.run(['taskkill', '/F', '/IM', 'explorer.exe'], capture_output=True)
    time.sleep(1.2)
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq explorer.exe'],
                         capture_output=True, text=True).stdout or ''
    if 'explorer.exe' not in out:
        subprocess.Popen(['explorer.exe'])

# ---------------------------------------------------------------- floating ball (360-style)

gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

class _SIZE(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]

class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_byte), ('BlendFlags', ctypes.c_byte),
                ('SourceConstantAlpha', ctypes.c_byte), ('AlphaFormat', ctypes.c_byte)]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]

class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('hwndTrack', wintypes.HWND), ('dwHoverTime', wintypes.DWORD)]

class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', RECT), ('rcWork', RECT), ('dwFlags', wintypes.DWORD)]

user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(_SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
user32.SetTimer.argtypes = [wintypes.HWND, wintypes.LPARAM, wintypes.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wintypes.HWND, wintypes.LPARAM]
user32.GetDpiForSystem.restype = wintypes.UINT
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
WM_APP_REASSERT = WM_APP + 1  # 球线程内部：重新置顶/落位

ULW_ALPHA = 2
AC_SRC_ALPHA = 1
MONITOR_DEFAULTTONEAREST = 2
TME_LEAVE = 0x0002
WM_MOUSELEAVE = 0x02A3
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
MF_POPUP = 0x00000010
MF_CHECKED = 0x00000008
MF_SEPARATOR = 0x00000800

def _seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

def render_ball_pixels(size=56):
    """BGRA premultiplied pixels for the layered ball (brand: #8b9dff circle + white W)."""
    ss = 4
    center = size / 2.0
    r_outer = size / 2.0 - 1.0
    rim = max(1.5, size * 0.036)
    base = (139, 157, 255)
    rim_col = (94, 112, 240)
    white = (255, 255, 255)
    scale = size / 32.0
    segs = [((8 * scale, 10 * scale), (12 * scale, 22 * scale)),
            ((12 * scale, 22 * scale), (16 * scale, 14 * scale)),
            ((16 * scale, 14 * scale), (20 * scale, 22 * scale)),
            ((20 * scale, 22 * scale), (24 * scale, 10 * scale))]
    stroke_half = 2.5 * scale / 2.0
    buf = bytearray(size * size * 4)
    for j in range(size):
        for i in range(size):
            cover = 0
            stroke_cov = 0.0
            for syy in range(ss):
                for sxx in range(ss):
                    x = i + (sxx + 0.5) / ss
                    y = j + (syy + 0.5) / ss
                    if math.hypot(x - center, y - center) <= r_outer:
                        cover += 1
                        if min(_seg_dist(x, y, a, b) for a, b in segs) <= stroke_half:
                            stroke_cov += 1.0
            if cover == 0:
                continue
            total = ss * ss
            alpha = int(round(238 * cover / total))
            t = min(1.0, stroke_cov * total / max(cover, 1))
            color = tuple(int(base[c] * (1 - t) + white[c] * t) for c in range(3))
            r_edge = (i + 0.5 - center) ** 2 + (j + 0.5 - center) ** 2
            if r_edge > (r_outer - rim) ** 2 and stroke_cov == 0:
                color = rim_col
            b_, g_, r_, a_ = color[2], color[1], color[0], alpha
            off = (j * size + i) * 4
            buf[off] = b_ * a_ // 255
            buf[off + 1] = g_ * a_ // 255
            buf[off + 2] = r_ * a_ // 255
            buf[off + 3] = a_
    return bytes(buf)

def _short16(v):
    return ctypes.c_short(v & 0xFFFF).value

def _work_area(pt):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    hmon = user32.MonitorFromPoint(wintypes.POINT(int(pt[0]), int(pt[1])), MONITOR_DEFAULTTONEAREST)
    if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return mi.rcWork
    return None

class BallHost:
    """Always-on-top draggable floating ball with edge-tuck + hover-expand (360 style)."""

    TUCK_VISIBLE = 10      # 收起后露出的像素
    SNAP_THRESHOLD = 48    # 距边缘多少像素内吸附

    def __init__(self, size=56, on_menu=None, on_move_end=None):
        self.size = size              # 逻辑尺寸（56 逻辑像素 ≈ 360 悬浮球大小）
        self.on_menu = on_menu
        self.on_move_end = on_move_end
        self.hwnd = 0
        self.thread = None
        self._wndproc_ref = WNDPROC(self._dispatch)
        self._anchor = None       # 完全可见时的左上角，物理像素（持久化用）；None=未初始化
        self._cur = [0, 0]
        self._target = None
        self._tucked = True
        self._edge = None
        self._drag = None
        self._dragging = False
        self._hbmp = 0
        self._hdcmem = 0
        self._hdc_screen = 0

    # ---- lifecycle ----
    def start(self, x=None, y=None, tucked=True):
        if x is not None and y is not None:
            self._anchor = [int(x), int(y)]
        self._tucked = bool(tucked)
        self.thread = threading.Thread(target=self._run, name='winctl-ball', daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.hwnd:
                break
            time.sleep(0.02)

    def _run(self):
        # windowed exe 的 stderr 不可见，球线程崩溃落盘诊断日志
        try:
            self._run_inner()
        except Exception:
            try:
                import traceback
                log = os.path.join(os.environ.get('TEMP', ''), 'WinCtlPanel3-ball.log')
                with open(log, 'a', encoding='utf-8') as f:
                    f.write('=== %s ===\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
                    traceback.print_exc(file=f)
            except Exception:
                pass

    def _run_inner(self):
        # 本线程全程物理像素：避开未感知进程的 DPI 虚拟化（否则 ULW 物理尺寸 vs 光标逻辑坐标错位）
        try:
            user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except Exception:
            pass
        try:
            dpi = user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        self.scale = max(1.0, dpi / 96.0)
        self.size_phys = int(round(self.size * self.scale))
        self.tuck_visible = int(round(self.TUCK_VISIBLE * self.scale))
        self.snap_threshold = int(round(self.SNAP_THRESHOLD * self.scale))
        self._pixels = render_ball_pixels(self.size_phys)

        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = 'WinCtlPanel3Ball'
        if not user32.RegisterClassW(ctypes.byref(wc)):
            return
        ex_style = 0x00080000 | 0x00000008 | 0x00000080   # LAYERED | TOPMOST | TOOLWINDOW
        style = 0x80000000 | 0x10000000                   # POPUP | VISIBLE
        self.hwnd = user32.CreateWindowExW(ex_style, wc.lpszClassName, '', style,
                                           0, 0, self.size_phys, self.size_phys, None, None, hinst, None)
        if not self.hwnd:
            return
        if self._anchor is None:  # 无存档位置：屏幕右缘垂直居中，收起待命
            vsx = user32.GetSystemMetrics(76)
            vsy = user32.GetSystemMetrics(77)
            vcx = user32.GetSystemMetrics(78)
            vcy = user32.GetSystemMetrics(79)
            margin = int(round(12 * self.scale))
            self._anchor = [vsx + vcx - self.size_phys - margin, vsy + vcy // 2 - self.size_phys // 2]
        if not self._paint():
            return
        self._anchor = list(self._clamp(self._anchor))
        self._cur = list(self._anchor)
        if self._tucked:
            self._edge = self._nearest_edge(self._anchor)[0]
            self._cur = list(self._tuck_pos())
        self._apply()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def show(self):
        # 跨线程只做无坐标操作（ShowWindow）；置顶与定位由球线程按物理坐标完成
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 5)  # SW_SHOW
            user32.PostMessageW(self.hwnd, WM_APP_REASSERT, 0, 0)

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)  # SW_HIDE

    # ---- geometry ----
    def _clamp(self, pos):
        vsx = user32.GetSystemMetrics(76)
        vsy = user32.GetSystemMetrics(77)
        vcx = user32.GetSystemMetrics(78)
        vcy = user32.GetSystemMetrics(79)
        x = max(vsx, min(int(pos[0]), vsx + vcx - self.size))
        y = max(vsy, min(int(pos[1]), vsy + vcy - self.size))
        return [x, y]

    def _nearest_edge(self, pos):
        wa = _work_area((pos[0] + self.size / 2.0, pos[1] + self.size / 2.0))
        if wa is None:
            return (None, wa)
        dists = {
            'left': int(pos[0]) - wa.left,
            'right': wa.right - (int(pos[0]) + self.size),
            'top': int(pos[1]) - wa.top,
            'bottom': wa.bottom - (int(pos[1]) + self.size),
        }
        edge = min(dists, key=dists.get)
        return (edge if dists[edge] < self.snap_threshold else None, wa)

    def _tuck_pos(self):
        wa = _work_area((self._anchor[0] + self.size_phys / 2.0, self._anchor[1] + self.size_phys / 2.0))
        if wa is None or self._edge is None:
            return self._anchor
        v = self.tuck_visible
        if self._edge == 'right':
            return [wa.right - v, self._anchor[1]]
        if self._edge == 'left':
            return [wa.left - self.size + v, self._anchor[1]]
        if self._edge == 'top':
            return [self._anchor[0], wa.top - self.size + v]
        return [self._anchor[0], wa.bottom - v]

    def _apply(self):
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), int(self._cur[0]), int(self._cur[1]),
                            0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _set_target(self, pos):
        self._target = [int(pos[0]), int(pos[1])]
        user32.SetTimer(self.hwnd, 1, 15, None)

    # ---- painting ----
    def _paint(self):
        self._hdc_screen = user32.GetDC(None)
        self._hdcmem = gdi32.CreateCompatibleDC(self._hdc_screen)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = self.size_phys
        bi.bmiHeader.biHeight = -self.size_phys  # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        ppv = ctypes.c_void_p(0)
        self._hbmp = gdi32.CreateDIBSection(self._hdcmem, ctypes.byref(bi), 0, ctypes.byref(ppv), None, 0)
        if not self._hbmp or not ppv:
            return False
        ctypes.memmove(ppv, self._pixels, len(self._pixels))
        gdi32.SelectObject(self._hdcmem, self._hbmp)
        pt_dst = wintypes.POINT(int(self._cur[0]), int(self._cur[1]))
        sz = _SIZE(self.size_phys, self.size_phys)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        return bool(user32.UpdateLayeredWindow(self.hwnd, self._hdc_screen, ctypes.byref(pt_dst),
                                               ctypes.byref(sz), self._hdcmem, ctypes.byref(pt_src),
                                               0, ctypes.byref(blend), ULW_ALPHA))

    # ---- window proc ----
    def _dispatch(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_APP_REASSERT:
                self._apply()
                return 0
            if msg == WM_LBUTTONDOWN:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                self._drag = (pt.x - rect.left, pt.y - rect.top)
                self._dragging = False
                self._target = None
                user32.KillTimer(hwnd, 1)
                user32.KillTimer(hwnd, 2)
                user32.SetCapture(hwnd)
                return 0
            if msg == 0x0200:  # WM_MOUSEMOVE
                x = _short16(lparam)
                y = _short16(int(lparam) >> 16)
                if self._drag is not None:
                    if not self._dragging and (abs(x - self._cur[0] - self._drag[0]) > 4
                                               or abs(y - self._cur[1] - self._drag[1]) > 4):
                        self._dragging = True
                    if self._dragging:
                        self._cur = [x - self._drag[0], y - self._drag[1]]
                        self._edge = None
                        self._apply()
                elif self._tucked:
                    tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                    user32.TrackMouseEvent(ctypes.byref(tme))
                    self._set_target(self._anchor)
                return 0
            if msg == 0x0202:  # WM_LBUTTONUP
                user32.ReleaseCapture()
                if self._drag is not None:
                    if self._dragging:
                        self._anchor = list(self._clamp(self._cur))
                        self._edge = self._nearest_edge(self._anchor)[0]
                        self._tucked = self._edge is not None
                        if self.on_move_end is not None:
                            try:
                                self.on_move_end(self._anchor[0], self._anchor[1], self._tucked)
                            except Exception:
                                pass
                        if self._tucked:
                            self._set_target(self._tuck_pos())
                    else:
                        if self.on_menu is not None:
                            self.on_menu(hwnd)
                    self._drag = None
                    self._dragging = False
                return 0
            if msg == WM_MOUSELEAVE:
                if self._tucked and self._drag is None:
                    self._set_target(self._tuck_pos())
                return 0
            if msg == WM_TIMER:
                if not self._target:
                    user32.KillTimer(hwnd, 1)
                    return 0
                dx = self._target[0] - self._cur[0]
                dy = self._target[1] - self._cur[1]
                dist = math.hypot(dx, dy)
                if dist <= 6:
                    self._cur = list(self._target)
                    self._target = None
                    user32.KillTimer(hwnd, 1)
                else:
                    step = max(6.0, dist / 4.0)
                    self._cur[0] += dx / dist * step
                    self._cur[1] += dy / dist * step
                self._apply()
                return 0
            if msg == WM_DESTROY:
                if self._hbmp:
                    gdi32.DeleteObject(self._hbmp)
                    self._hbmp = 0
                if self._hdcmem:
                    gdi32.DeleteDC(self._hdcmem)
                    self._hdcmem = 0
                if self._hdc_screen:
                    user32.ReleaseDC(None, self._hdc_screen)
                    self._hdc_screen = 0
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


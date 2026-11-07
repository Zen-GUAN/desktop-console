# -*- coding: utf-8 -*-
"""Desktop Console v3 — Python host (route A: pywebview + PyInstaller).

Backend parity with v2-web/server/Server.ps1:
  * same REST surface (/api/*) and Chinese error messages, so ui/* works unchanged
  * same backup semantics (%APPDATA%\\WinCtlPanel\\backup.json, first-write-wins per key)
  * monitors JSON is a FLAT array of objects (v2's nested-array bug must not regress;
    selftest guards the shape)
The frontend (v2-web/ui) is served as-is; it belongs to Kimi and is never modified here.

Usage:
  python app.py --selftest            run backend checks, exit code 0/1
  python app.py --no-window [--port N]  headless HTTP server (for API testing)
  python app.py [--port N]            full app: HTTP server + WebView2 window + tray
"""
import argparse
import ctypes
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import uuid
import winreg
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import winapi

APP_TITLE = '桌面控制台'
MUTEX_NAME = 'Local\\WinCtlPanel3_SingleInstance'
DATA_DIR = os.path.join(os.environ.get('APPDATA', ''), 'WinCtlPanel')
BACKUP_FILE = os.path.join(DATA_DIR, 'backup.json')
UI_STATE_FILE = os.path.join(os.environ.get('APPDATA', ''), 'WinCtlPanel2', 'ui.json')

# 悬浮球：功能自选清单（托盘右键 → 悬浮球 子菜单勾选），配置落 ball.json
BALL_FUNCS = [('panel', '显示/隐藏面板'), ('theme', '深浅色切换'), ('bright_up', '亮度 +5'),
              ('bright_down', '亮度 -5'), ('restore', '一键还原'), ('wallpaper', '更换壁纸'),
              ('wallpaper_restore', '仅还原壁纸'), ('explorer', '重启资源管理器'), ('exit', '退出程序')]
BALL_FUNC_LABELS = dict(BALL_FUNCS)
BALL_DEFAULT = ['panel', 'theme', 'bright_up', 'bright_down', 'restore']
BALL_CONFIG_FILE = os.path.join(os.environ.get('APPDATA', ''), 'WinCtlPanel2', 'ball.json')
BALL = {'enabled': True, 'functions': list(BALL_DEFAULT), 'x': None, 'y': None, 'tucked': True}
GUI_HOST = None

HKEY_CURRENT_USER = winreg.HKEY_CURRENT_USER
THEME_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
ADV_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.bmp')
CONTENT_TYPES = {'.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
                 '.svg': 'image/svg+xml', '.html': 'text/html; charset=utf-8',
                 '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.bmp': 'image/bmp'}

SERVER_TOKEN = ''
SERVER_PORT = 0
EXIT_EVENT = threading.Event()

# runtime flags shared across threads
STATE = {'topmost': False, 'rotation': False, 'folder': '',
         'hotkeys': {'brightnessUp': False, 'brightnessDown': False, 'theme': False}}
MONITORS = []          # [(handle, desc)]
MONITOR_LOCK = threading.Lock()
ROTATE_THREAD = None


class ApiError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


def base_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def serve_base():
    """Directory that contains ui\\ (mirror of Server.ps1 $script:Root)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'v2-web'))


def icon_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'icon.ico')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')


def load_ball_config():
    try:
        if os.path.exists(BALL_CONFIG_FILE):
            with open(BALL_CONFIG_FILE, encoding='utf-8-sig') as f:
                data = json.load(f)
            BALL['enabled'] = bool(data.get('enabled', True))
            funcs = data.get('functions')
            if isinstance(funcs, list):
                known = {fid for fid, _label in BALL_FUNCS}
                BALL['functions'] = [fid for fid in funcs if fid in known]
            if isinstance(data.get('x'), int) and isinstance(data.get('y'), int):
                BALL['x'], BALL['y'] = data['x'], data['y']
            BALL['tucked'] = bool(data.get('tucked', True))
    except Exception:
        pass


def save_ball_config():
    try:
        os.makedirs(os.path.dirname(BALL_CONFIG_FILE), exist_ok=True)
        tmp = BALL_CONFIG_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'enabled': BALL['enabled'], 'functions': BALL['functions'],
                       'x': BALL['x'], 'y': BALL['y'], 'tucked': BALL['tucked']},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, BALL_CONFIG_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------- 目标机环境检测（通用性）

WEBVIEW2_CLIENT_GUID = r'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
WEBVIEW2_DOWNLOAD_URL = 'https://go.microsoft.com/fwlink/p/?LinkID=2124703'  # Evergreen 引导器


def webview2_runtime_installed():
    """WebView2 Evergreen 运行时是否可用（Win11 自带；Win10 需装 Edge 或 Runtime）。"""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in ('SOFTWARE', 'SOFTWARE\\WOW6432Node'):
            try:
                with winreg.OpenKey(root, base + '\\' + WEBVIEW2_CLIENT_GUID) as key:
                    pv = str(winreg.QueryValueEx(key, 'pv')[0])
                    if pv and pv != '0.0.0.0':
                        return True
            except OSError:
                continue
    return False


def windows_version_info():
    """(名称, build 号, DisplayVersion)。build >= 22000 为 Win11。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion') as key:
            build = int(winreg.QueryValueEx(key, 'CurrentBuildNumber')[0])
            try:
                display = str(winreg.QueryValueEx(key, 'DisplayVersion')[0])
            except OSError:
                display = ''
            return ('Windows 11' if build >= 22000 else 'Windows 10'), build, display
    except OSError:
        return 'Windows', 0, ''


def dotnet_framework_ok():
    """.NET Framework 4.6.2+（pythonnet/GUI 宿主需要）。4.6.2 的 Release=394802。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
            return int(winreg.QueryValueEx(key, 'Release')[0]) >= 394802
    except OSError:
        return False


# ---------------------------------------------------------------- backup (parity with Add-Backup)

class Backup:
    def __init__(self):
        self.lock = threading.Lock()
        self.seen = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, encoding='utf-8-sig') as f:  # PS 5.1 写出的文件带 BOM
                    data = json.load(f)
                for key in (data.get('values') or {}):
                    self.seen[key] = True
            except Exception:
                raise RuntimeError('已有备份文件损坏。为避免覆盖原始值，面板不会执行修改。')

    def add(self, key, value):
        with self.lock:
            if self.seen.get(key):
                return
            data = {'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'values': {}}
            if os.path.exists(BACKUP_FILE):
                try:
                    with open(BACKUP_FILE, encoding='utf-8-sig') as f:
                        data = json.load(f)
                    if not isinstance(data.get('values'), dict):
                        data['values'] = {}
                except Exception:
                    raise RuntimeError('备份文件无法读取，已停止修改。请先处理 %APPDATA%\\WinCtlPanel\\backup.json。')
            data['values'].setdefault(key, value)
            tmp = BACKUP_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, BACKUP_FILE)
            self.seen[key] = True

    def exists(self):
        return os.path.exists(BACKUP_FILE)

    def load(self):
        with open(BACKUP_FILE, encoding='utf-8-sig') as f:
            return json.load(f)

    def clear(self):
        try:
            os.remove(BACKUP_FILE)
        except OSError:
            pass
        self.seen = {}


BACKUP = None  # created in main()


# ---------------------------------------------------------------- monitors

def refresh_monitors():
    global MONITORS
    with MONITOR_LOCK:
        for handle, _desc in MONITORS:
            winapi.destroy_monitor(handle)
        MONITORS = winapi.enum_physical_monitors()
        return list(MONITORS)


def monitor_list():
    with MONITOR_LOCK:
        return list(MONITORS)


def monitor_state():
    result = []
    items = monitor_list()
    for i, (handle, desc) in enumerate(items):
        info = winapi.get_brightness(handle)
        supported = info is not None
        mn, cur, mx = info if supported else (0, 0, 0)
        span = int(mx) - int(mn)
        pct = int(round((int(cur) - int(mn)) * 100.0 / span)) if (supported and span > 0) else 0
        result.append({'index': i, 'name': desc, 'supported': supported, 'percent': pct,
                       'raw': int(cur) if supported else 0,
                       'min': int(mn) if supported else 0, 'max': int(mx) if supported else 0})
    # 与 PS 版一致：monitors 必须是「平铺的对象数组」，任何嵌套都是 bug（v2 亮度误报的根因）
    return result


def build_state():
    dark = winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AppsUseLightTheme', 1) == 0
    accent = winapi.abgr_to_hex(winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AccentColor', 0))
    taskbar = int(winapi.get_reg_dword(HKEY_CURRENT_USER, ADV_KEY, 'TaskbarAl', 1))
    return {'ok': True, 'monitors': monitor_state(), 'dark': dark, 'accent': accent,
            'taskbar': taskbar, 'wallpaper': winapi.get_wallpaper(), 'backup': BACKUP.exists(),
            'topmost': STATE['topmost'], 'rotation': STATE['rotation'], 'rotateFolder': STATE['folder'],
            'hotkeys': dict(STATE['hotkeys']), 'offline': True,
            'ball': {'enabled': BALL['enabled'], 'functions': list(BALL['functions'])}}


# ---------------------------------------------------------------- shared operations (handlers + hotkeys)

def apply_theme(dark):
    BACKUP.add('appsLight', winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AppsUseLightTheme', 1))
    BACKUP.add('sysLight', winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'SystemUsesLightTheme', 1))
    value = 0 if dark else 1
    winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AppsUseLightTheme', value)
    winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'SystemUsesLightTheme', value)
    winapi.broadcast_settings('ImmersiveColorSet')


def step_brightness(step):
    for i, (handle, _desc) in enumerate(monitor_list()):
        info = winapi.get_brightness(handle)
        if info is None:
            continue
        mn, cur, mx = (int(v) for v in info)
        span = mx - mn
        if span <= 0:
            continue
        pct = int(round((cur - mn) * 100.0 / span)) + step
        pct = max(0, min(100, pct))
        raw = mn + int(round(pct * span / 100.0))
        BACKUP.add('bright:%d' % i, '%d;%d;%d' % (mn, cur, mx))
        winapi.set_brightness(handle, raw)


def set_wallpaper_file(path):
    BACKUP.add('wallpaper', winapi.get_wallpaper())
    if not winapi.set_wallpaper(path):
        raise ApiError('系统拒绝了壁纸设置，请确认图片存在且格式为 JPG、PNG 或 BMP。')
    winapi.broadcast_settings(None)


def do_wallpaper_pick(owner=0):
    """选择并设置壁纸；返回 {'cancelled': True} 或 None（成功）。供 API 与悬浮球共用。"""
    hwnd = owner or winapi.find_main_hwnd(APP_TITLE)
    file = winapi.open_image_dialog(hwnd)
    if not file:
        return {'ok': True, 'cancelled': True}
    set_wallpaper_file(file)
    return None


def do_wallpaper_restore():
    """仅还原壁纸。供 API 与悬浮球共用；失败抛 ApiError。"""
    if not BACKUP.exists():
        raise ApiError('还没有记录原壁纸，本次尚未更换过壁纸。')
    values = (BACKUP.load().get('values') or {})
    if 'wallpaper' not in values:
        raise ApiError('备份中没有原壁纸记录。')
    if not winapi.set_wallpaper(str(values['wallpaper'])):
        raise ApiError('系统拒绝恢复原壁纸，请确认原图片仍然存在。')
    winapi.broadcast_settings(None)


def ball_config_payload():
    """悬浮球设置的完整 payload（GET 响应 / POST 保存后的回执）。"""
    return {'ok': True, 'enabled': BALL['enabled'], 'functions': list(BALL['functions']),
            'available': [{'id': fid, 'label': label} for fid, label in BALL_FUNCS]}


def apply_ball_enabled():
    """按 BALL['enabled'] 显示/隐藏悬浮球；GUI 不在场（--no-window）时静默跳过。"""
    if GUI_HOST is None:
        return
    if BALL['enabled']:
        GUI_HOST.ensure_ball()
    elif GUI_HOST.ball is not None:
        GUI_HOST.ball.hide()


def ball_action(fid):
    """悬浮球点击菜单的动作分发。"""
    try:
        if fid == 'panel':
            hwnd = winapi.find_main_hwnd(APP_TITLE)  # 只搜可见窗口：可见→隐藏，否则→显示
            if hwnd and GUI_HOST is not None:
                GUI_HOST.hide_window()
            elif GUI_HOST is not None:
                GUI_HOST.show_window()
        elif fid == 'theme':
            dark = winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AppsUseLightTheme', 1) == 0
            apply_theme(not dark)
        elif fid == 'bright_up':
            step_brightness(5)
        elif fid == 'bright_down':
            step_brightness(-5)
        elif fid == 'restore':
            failed = restore_all()
            if failed:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, '部分项目未能还原，备份已保留：\n' + '\n'.join(failed),
                                                 APP_TITLE, 0x10)
        elif fid == 'wallpaper':
            do_wallpaper_pick()
        elif fid == 'wallpaper_restore':
            do_wallpaper_restore()
        elif fid == 'explorer':
            winapi.restart_explorer()
        elif fid == 'exit':
            EXIT_EVENT.set()
    except Exception as exc:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, '操作失败：%s' % exc, APP_TITLE, 0x10)


def restore_all():
    if not BACKUP.exists():
        return []
    data = BACKUP.load()
    failed = []
    for key, value in (data.get('values') or {}).items():
        try:
            if key == 'wallpaper':
                if not winapi.set_wallpaper(str(value)):
                    raise RuntimeError('系统拒绝恢复壁纸')
            elif key == 'appsLight':
                winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AppsUseLightTheme', int(value))
            elif key == 'sysLight':
                winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'SystemUsesLightTheme', int(value))
            elif key == 'accent':
                winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AccentColor', int(value))
            elif key == 'colorPrevalence':
                winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'ColorPrevalence', int(value))
            elif key == 'taskbarAl':
                winapi.set_reg_dword(HKEY_CURRENT_USER, ADV_KEY, 'TaskbarAl', int(value))
            elif key.startswith('bright:'):
                idx = int(key.split(':', 1)[1])
                monitors = winapi.enum_physical_monitors()
                if idx >= len(monitors):
                    raise RuntimeError('原显示器当前未连接')
                parts = str(value).split(';')
                if len(parts) < 3:
                    raise RuntimeError('亮度备份格式错误')
                if not winapi.set_brightness(monitors[idx][0], int(parts[1])):
                    raise RuntimeError('显示器拒绝恢复亮度，请检查 DDC/CI')
        except Exception as exc:
            failed.append('%s：%s' % (key, exc))
    winapi.broadcast_settings('ImmersiveColorSet')
    winapi.broadcast_settings(None)
    if not failed:
        BACKUP.clear()
    return failed


# ---------------------------------------------------------------- rotation thread

def rotate_loop():
    while True:
        for _ in range(600):  # 10 分钟，粒度 1s 便于快速退出
            if EXIT_EVENT.is_set():
                return
            time.sleep(1)
        if not STATE['rotation']:
            continue
        try:
            folder = STATE['folder']
            files = []
            if folder and os.path.isdir(folder):
                for root, _dirs, names in os.walk(folder):
                    for name in names:
                        if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                            files.append(os.path.join(root, name))
            if files:
                set_wallpaper_file(files[random.randrange(len(files))])
        except Exception:
            pass


# ---------------------------------------------------------------- API operations

def handle_api(op, method, body):
    """Returns (payload, status, after_fn|None). Raises ApiError on failure."""
    if op == 'state':
        return build_state(), 200, None

    if op == 'brightness':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        idx = int(body.get('index', 0))
        pct = max(0, min(100, int(body.get('percent', 0))))
        items = monitor_list()
        if idx < 0 or idx >= len(items):
            raise ApiError('没有找到这台显示器，请重新打开面板检测。')
        handle = items[idx][0]
        info = winapi.get_brightness(handle)
        if info is None:
            raise ApiError('你的显示器不支持 DDC/CI，或在显示器菜单里关闭了它。')
        mn, cur, mx = (int(v) for v in info)
        span = mx - mn
        raw = mn + (int(round(pct * span / 100.0)) if span > 0 else 0)
        BACKUP.add('bright:%d' % idx, '%d;%d;%d' % (mn, cur, mx))
        if not winapi.set_brightness(handle, raw):
            raise ApiError('亮度设置失败。你的显示器可能不支持 DDC/CI，或在显示器菜单里关闭了它。')
        time.sleep(0.1)
        return {'ok': True, 'backup': True, 'state': build_state()}, 200, None

    if op == 'theme':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        apply_theme(bool(body.get('dark')))
        return build_state(), 200, None

    if op == 'accent':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        color = str(body.get('color', ''))
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ApiError('请选择有效的六位十六进制颜色。')
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        BACKUP.add('accent', winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AccentColor', 0))
        BACKUP.add('colorPrevalence', winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'ColorPrevalence', 0))
        winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AccentColor', winapi.rgb_to_abgr(r, g, b))
        winapi.set_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'ColorPrevalence', 1)
        winapi.broadcast_settings('ImmersiveColorSet')
        return build_state(), 200, None

    if op == 'taskbar':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        value = int(body.get('value', 1))
        if value not in (0, 1):
            raise ApiError('任务栏对齐只能是左对齐或居中。')
        BACKUP.add('taskbarAl', winapi.get_reg_dword(HKEY_CURRENT_USER, ADV_KEY, 'TaskbarAl', 1))
        winapi.set_reg_dword(HKEY_CURRENT_USER, ADV_KEY, 'TaskbarAl', value)
        winapi.broadcast_settings(None)
        return build_state(), 200, None

    if op == 'wallpaper/pick':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        result = do_wallpaper_pick()
        if result is not None and result.get('cancelled'):
            return {'ok': True, 'cancelled': True}, 200, None
        return build_state(), 200, None

    if op == 'wallpaper-preview':
        file = winapi.get_wallpaper()
        ext = os.path.splitext(file)[1].lower() if file else ''
        if not file or ext not in IMAGE_EXTS or not os.path.isfile(file):
            raise ApiError('没有可用的本地壁纸预览。', 404)
        with open(file, 'rb') as f:
            data = f.read()
        return ('RAW', data, CONTENT_TYPES.get(ext, 'application/octet-stream')), 200, None

    if op == 'wallpaper/restore':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        do_wallpaper_restore()
        return build_state(), 200, None

    if op == 'wallpaper/rotate':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        if STATE['rotation']:
            STATE['rotation'] = False
            STATE['folder'] = ''
        else:
            hwnd = winapi.find_main_hwnd(APP_TITLE)
            folder = winapi.browse_folder_dialog(hwnd, '选择壁纸轮换文件夹')
            if not folder:
                return {'ok': True, 'cancelled': True}, 200, None
            STATE['folder'] = folder
            STATE['rotation'] = True
            global ROTATE_THREAD
            if ROTATE_THREAD is None or not ROTATE_THREAD.is_alive():
                ROTATE_THREAD = threading.Thread(target=rotate_loop, name='winctl-rotate', daemon=True)
                ROTATE_THREAD.start()
        return build_state(), 200, None

    if op == 'restore':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        failed = restore_all()
        if failed:
            raise ApiError('部分项目未能还原，备份已保留：' + '；'.join(failed))
        return build_state(), 200, None

    if op == 'ball/config':
        if method == 'GET':
            return ball_config_payload(), 200, None
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        if 'enabled' in body:
            BALL['enabled'] = bool(body.get('enabled'))
        if 'functions' in body:
            funcs = body.get('functions')
            if not isinstance(funcs, list):
                raise ApiError('功能列表格式不正确。')
            known = {fid for fid, _label in BALL_FUNCS}
            ordered = []
            for fid in funcs:
                if not isinstance(fid, str) or fid not in known:
                    raise ApiError('功能列表包含未知项：%s' % fid)
                if fid not in ordered:
                    ordered.append(fid)
            BALL['functions'] = ordered
        save_ball_config()
        apply_ball_enabled()
        return ball_config_payload(), 200, None

    if op == 'window/topmost':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        value = bool(body.get('value'))
        STATE['topmost'] = value
        hwnd = winapi.find_main_hwnd(APP_TITLE)
        winapi.apply_topmost(hwnd, value)
        try:
            os.makedirs(os.path.dirname(UI_STATE_FILE), exist_ok=True)
            with open(UI_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump({'topmost': value}, f)
        except Exception:
            pass
        return {'ok': True, 'topmost': value}, 200, None

    if op == 'window/hide':
        def after():
            gui = getattr(handle_api, 'gui', None)
            if gui is not None and getattr(gui, 'tray_ok', False):
                gui.hide_window()
            elif gui is not None:
                gui.minimize_window()
        return {'ok': True}, 200, after

    if op == 'window/drag':
        def after():
            hwnd = winapi.find_main_hwnd(APP_TITLE)
            if hwnd:
                winapi.begin_title_drag(hwnd)
        return {'ok': True}, 200, after

    if op == 'window/resize':
        def after():
            hwnd = winapi.find_main_hwnd(APP_TITLE)
            if hwnd:
                winapi.begin_resize(hwnd)
        return {'ok': True}, 200, after

    if op == 'advanced/theme':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        hwnd = winapi.find_main_hwnd(APP_TITLE)
        file = winapi.open_theme_dialog(hwnd)
        if not file:
            return {'ok': True, 'cancelled': True}, 200, None
        import subprocess
        arg = 'shell32.dll,Control_RunDLL desk.cpl desk,@Themes /Action:OpenTheme /file:"%s"' % file
        subprocess.Popen(['rundll32.exe', arg])
        return {'ok': True, 'cancelled': False}, 200, None

    if op == 'advanced/lockscreen':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        hwnd = winapi.find_main_hwnd(APP_TITLE)
        file = winapi.open_image_dialog(hwnd)
        if not file:
            return {'ok': True, 'cancelled': True}, 200, None
        img = file.replace("'", "''").replace('"', '')
        cmd = ("New-Item -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization' -Force | Out-Null; "
               "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization' "
               "-Name 'LockScreenImage' -Value '%s'; "
               "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization' "
               "-Name 'LockScreenImageStatus' -Value 1 -Type DWord") % img
        code = winapi.run_elevated_powershell('-NoProfile -NonInteractive -WindowStyle Hidden -Command "%s"' % cmd)
        if code != 0:
            raise ApiError('锁屏设置未完成（UAC 被取消或系统拒绝）。')
        return {'ok': True, 'cancelled': False}, 200, None

    if op == 'advanced/restart-explorer':
        def after():
            winapi.restart_explorer()
        return {'ok': True}, 200, after

    if op == 'exit':
        if method != 'POST':
            raise ApiError('不支持的请求方式。')
        EXIT_EVENT.set()
        return {'ok': True}, 200, None

    raise ApiError('未知操作。', 404)


# ---------------------------------------------------------------- HTTP server

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _send(self, payload, status=200, content_type='application/json; charset=utf-8', raw=None):
        if raw is not None:
            body = raw
        elif isinstance(payload, tuple) and len(payload) == 3 and payload[0] == 'RAW':
            body, content_type = payload[1], payload[2]
        else:
            body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def _route(self, method):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path.startswith('/api/'):
                return self._api(path[len('/api/'):], method)
            if method != 'GET':
                return self._send({'ok': False, 'message': '不支持的请求方式。'}, 405)
            return self._static(path)
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as exc:
            try:
                self._send({'ok': False, 'message': str(exc) or '操作失败。'}, 500)
            except Exception:
                pass

    def _api(self, op, method):
        token = self.headers.get('X-WinCtl-Token')
        if token != SERVER_TOKEN:
            return self._send({'ok': False, 'message': '本地请求验证失败。'}, 403)
        origin = self.headers.get('Origin')
        if origin and origin != 'http://127.0.0.1:%d' % SERVER_PORT:
            return self._send({'ok': False, 'message': '已拒绝跨站请求。'}, 403)
        body = {}
        if method == 'POST':
            try:
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b'{}'
                body = json.loads(raw.decode('utf-8') or '{}')
            except Exception:
                return self._send({'ok': False, 'message': '请求内容无法解析。'}, 500)
            if not isinstance(body, dict):
                return self._send({'ok': False, 'message': '请求内容无法解析。'}, 500)
        try:
            payload, status, after = handle_api(op, method, body)
        except ApiError as exc:
            return self._send({'ok': False, 'message': str(exc)}, exc.status)
        except Exception as exc:
            return self._send({'ok': False, 'message': str(exc) or '操作失败。'}, 500)
        self._send(payload, status)
        if after is not None:
            try:
                after()
            except Exception:
                pass

    def _static(self, path):
        if path == '/':
            index = os.path.join(serve_base(), 'ui', 'index.html')
            with open(index, encoding='utf-8-sig') as f:
                html = f.read().replace('__WINCTL_TOKEN__', SERVER_TOKEN)
            return self._send(None, 200, 'text/html; charset=utf-8', raw=html.encode('utf-8'))
        rel = urllib.parse.unquote(path).strip('/').replace('/', os.sep)
        root = os.path.abspath(os.path.join(serve_base(), 'ui')) + os.sep
        full = os.path.abspath(os.path.join(serve_base(), rel))
        if not full.startswith(root) or not os.path.isfile(full):
            return self._send({'ok': False, 'message': '资源不存在。'}, 404)
        ext = os.path.splitext(full)[1].lower()
        with open(full, 'rb') as f:
            data = f.read()
        self._send(None, 200, CONTENT_TYPES.get(ext, 'application/octet-stream'), raw=data)


# ---------------------------------------------------------------- selftest

def run_selftest():
    global BACKUP_FILE, BACKUP
    failures = []
    lines = []
    if BACKUP is None:
        BACKUP = Backup()  # 只读初始化（仅读现有 backup.json 的键），不写盘

    def emit(text):
        lines.append(text)
        print(text)

    def check(name, ok, detail=''):
        emit('[%s] %s  %s' % ('PASS' if ok else 'FAIL', name, detail))
        if not ok:
            failures.append(name)

    monitors = winapi.enum_physical_monitors()
    check('enumerate-monitors', len(monitors) > 0, 'count=%d' % len(monitors))

    ddc = False
    detail = 'no monitor'
    if monitors:
        info = winapi.get_brightness(monitors[0][0])
        if info is not None:
            ddc = True
            detail = '%s: %d/%d/%d' % (monitors[0][1], info[0], info[1], info[2])
        else:
            detail = 'DDC/CI not available'
    check('ddc-read-brightness', ddc, detail)

    accent_ok = (winapi.abgr_to_hex(winapi.rgb_to_abgr(255, 0, 0)) == '#FF0000'
                 and winapi.abgr_to_hex(winapi.rgb_to_abgr(0, 0, 255)) == '#0000FF')
    check('accent-convert', accent_ok, 'round-trip')

    # registry DWORD round-trip with a big (0xFFxxxxxx) value — the accent-color case
    test_key = r'Software\WinCtlPanel3\_selftest'
    try:
        winapi.set_reg_dword(HKEY_CURRENT_USER, test_key, 'TestValue', winapi.rgb_to_abgr(139, 157, 255))
        readback = winapi.get_reg_dword(HKEY_CURRENT_USER, test_key, 'TestValue', -1)
        check('registry-dword', readback == winapi.rgb_to_abgr(139, 157, 255), hex(readback & 0xFFFFFFFF))
    except Exception as exc:
        check('registry-dword', False, str(exc))
    finally:
        try:
            with winreg.OpenKey(HKEY_CURRENT_USER, r'Software\WinCtlPanel3', 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteKey(k, '_selftest')
        except OSError:
            pass

    wp = winapi.get_wallpaper()
    check('wallpaper-readable', len(wp) > 0, wp[:60])

    # backup merge semantics against a temp file
    old_backup_file, old_backup = BACKUP_FILE, BACKUP
    tmp = os.path.join(DATA_DIR, 'backup_selftest_%s.json' % uuid.uuid4().hex)
    try:
        BACKUP_FILE = tmp
        BACKUP = Backup()
        BACKUP.add('taskbarAl', 1)
        BACKUP.add('taskbarAl', 0)
        stored = BACKUP.load().get('values', {}).get('taskbarAl')
        check('backup-merge-semantics', stored == 1, 'second write must not override (stored=%r)' % stored)
    except Exception as exc:
        check('backup-merge-semantics', False, str(exc))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
        BACKUP_FILE, BACKUP = old_backup_file, old_backup

    # THE shape regression: build_state()['monitors'] must be a flat list of objects
    try:
        refresh_monitors()
        state = build_state()
        state2 = json.loads(json.dumps(state))
        flat = (isinstance(state2['monitors'], list)
                and (len(state2['monitors']) == 0 or isinstance(state2['monitors'][0], dict)))
        check('state-json-shape', flat,
              'monitors[0].type=%s' % (type(state2['monitors'][0]).__name__ if state2['monitors'] else 'empty'))
    except Exception as exc:
        check('state-json-shape', False, str(exc))
    finally:
        for handle, _desc in monitor_list():
            winapi.destroy_monitor(handle)
        globals()['MONITORS'] = []

    # 悬浮球渲染：尺寸正确、四角透明、中心有像素、预乘合法
    try:
        buf = winapi.render_ball_pixels(56)
        center = buf[(28 * 56 + 28) * 4:(28 * 56 + 28) * 4 + 4]
        ok = (len(buf) == 56 * 56 * 4 and buf[3] == 0 and center[3] > 0
              and center[0] <= center[3] and center[2] <= center[3])
        check('ball-render', ok, 'center_alpha=%d corner_alpha=%d' % (center[3], buf[3]))
    except Exception as exc:
        check('ball-render', False, str(exc))

    # ---- 换机诊断：系统版本 / WebView2 / .NET ----
    try:
        name, build, display = windows_version_info()
        check('windows-version', build >= 14393, '%s %s build %d（需 Win10 1607+）' % (name, display, build))
        emit('[INFO] taskbar-align: %s' % ('Win11 生效' if build >= 22000 else '此系统任务栏对齐仅 Win11 生效'))
        check('webview2-runtime', webview2_runtime_installed(),
              'GUI 必需' if webview2_runtime_installed() else '缺失：GUI 无法启动，需安装 WebView2 Runtime')
        check('dotnet-framework', dotnet_framework_ok(), '.NET 4.6.2+（GUI 宿主需要）')
    except Exception as exc:
        check('env-diagnose', False, str(exc))

    emit('selftest: %d failure(s)' % len(failures))
    # windowed exe 无控制台，把结果同时落盘；冻结态再弹窗告知（人工运行 --selftest 时）
    try:
        log_path = os.path.join(os.environ.get('TEMP', ''), 'WinCtlPanel3-selftest.log')
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        if getattr(sys, 'frozen', False):
            ctypes.windll.user32.MessageBoxW(
                0, '自检完成：%d 项未通过。\n详情：%s' % (len(failures), log_path),
                APP_TITLE, 0x40)
    except Exception:
        pass
    return 1 if failures else 0


# ---------------------------------------------------------------- GUI host

class GuiHost:
    def __init__(self):
        self.window = None
        self.tray = None
        self.tray_ok = False
        self.ball = None

    def hide_window(self):
        try:
            self.window.hide()
        except Exception:
            hwnd = winapi.find_main_hwnd(APP_TITLE)
            if hwnd:
                import ctypes
                ctypes.windll.user32.ShowWindow(hwnd, 0)

    def minimize_window(self):
        try:
            self.window.minimize()
        except Exception:
            pass

    def show_window(self):
        try:
            self.window.show()
        except Exception:
            pass
        hwnd = winapi.find_main_hwnd(APP_TITLE)
        if hwnd:
            import ctypes
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)

    def set_topmost(self, value):
        STATE['topmost'] = bool(value)
        hwnd = winapi.find_main_hwnd(APP_TITLE)
        winapi.apply_topmost(hwnd, value)
        try:
            os.makedirs(os.path.dirname(UI_STATE_FILE), exist_ok=True)
            with open(UI_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump({'topmost': bool(value)}, f)
        except Exception:
            pass

    def run(self, port):
        import webview
        self.window = webview.create_window(
            APP_TITLE, 'http://127.0.0.1:%d/' % port,
            width=380, height=600, x=80, y=60, min_size=(340, 480))
        self.tray = winapi.TrayHost(
            APP_TITLE, icon_path(),
            on_show=self.show_window,
            on_topmost=self.set_topmost,
            on_exit=lambda: EXIT_EVENT.set(),
            on_hotkey=self._hotkey,
            on_hotkey_state=self._hotkey_state,
            on_build_menu=self._build_ball_menu,
            on_menu=self._tray_menu,
        )
        self.tray.start([(1, 3, 0x26), (2, 3, 0x28), (3, 3, 0x44)],
                        initial_topmost=STATE['topmost'])
        self.tray_ok = True
        handle_api.gui = self
        load_ball_config()
        if BALL['enabled']:
            self.ensure_ball()

        def watchdog():
            EXIT_EVENT.wait()
            # 优先让 GUI 线程走原生关闭流程（window.destroy 跨线程不保证生效）
            hwnd = winapi.find_main_hwnd(APP_TITLE)
            if hwnd:
                import ctypes
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            try:
                self.window.destroy()
            except Exception:
                pass
            # 硬兜底：8 秒内主循环仍未返回就强制退出，绝不留僵尸进程
            hard = threading.Timer(8.0, lambda: os._exit(0))
            hard.daemon = True
            hard.start()
        threading.Thread(target=watchdog, name='winctl-watchdog', daemon=True).start()
        webview.start(gui='edgechromium')

    # ---- 悬浮球 ----
    def ensure_ball(self):
        if self.ball is not None and self.ball.hwnd:
            self.ball.show()
            return
        # 默认位置由 BallHost 在球线程内按物理像素计算（BALL['x'] 为 None 时）
        self.ball = winapi.BallHost(size=56, on_menu=self._ball_menu, on_move_end=self._ball_moved)
        self.ball.start(BALL['x'], BALL['y'], tucked=BALL['tucked'])

    def _ball_moved(self, x, y, tucked):
        BALL['x'], BALL['y'], BALL['tucked'] = x, y, tucked
        save_ball_config()

    def _ball_menu(self, hwnd):
        menu = winapi.user32.CreatePopupMenu()
        ids = {}
        for offset, fid in enumerate(BALL['functions']):  # 按设置页/配置的顺序弹出
            label = BALL_FUNC_LABELS.get(fid, fid)
            winapi.user32.AppendMenuW(menu, 0, 100 + offset, label)
            ids[100 + offset] = fid
        if not ids:
            winapi.user32.AppendMenuW(menu, 0, 0, '（未选择功能：主面板 → 更多工具 → 悬浮球设置）')
        winapi.user32.SetForegroundWindow(hwnd)
        pt = wintypes.POINT()
        winapi.user32.GetCursorPos(ctypes.byref(pt))
        cmd = winapi.user32.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0080, pt.x, pt.y, 0, hwnd, None)
        winapi.user32.DestroyMenu(menu)
        winapi.user32.PostMessageW(hwnd, 0, 0, 0)
        fid = ids.get(cmd)
        if fid:
            ball_action(fid)

    def _build_ball_menu(self, parent_menu):
        """托盘右键菜单的「悬浮球」子菜单：开关 + 功能自选。"""
        sub = winapi.user32.CreatePopupMenu()
        winapi.user32.AppendMenuW(sub, winapi.MF_CHECKED if BALL['enabled'] else 0, 10, '启用悬浮球')
        winapi.user32.AppendMenuW(sub, winapi.MF_SEPARATOR, 0, None)
        for i, (fid, label) in enumerate(BALL_FUNCS):
            checked = fid in BALL['functions']
            winapi.user32.AppendMenuW(sub, winapi.MF_CHECKED if checked else 0, 20 + i, label)
        winapi.user32.AppendMenuW(parent_menu, winapi.MF_POPUP, sub, '悬浮球')

    def _tray_menu(self, cmd):
        if cmd == 10:
            BALL['enabled'] = not BALL['enabled']
            save_ball_config()
            apply_ball_enabled()
        elif 20 <= cmd < 20 + len(BALL_FUNCS):
            fid = BALL_FUNCS[cmd - 20][0]
            if fid in BALL['functions']:
                BALL['functions'].remove(fid)
            else:
                BALL['functions'].append(fid)
            save_ball_config()

    def _hotkey_state(self, hotkey_id, ok):
        if hotkey_id == 1:
            STATE['hotkeys']['brightnessUp'] = ok
        elif hotkey_id == 2:
            STATE['hotkeys']['brightnessDown'] = ok
        elif hotkey_id == 3:
            STATE['hotkeys']['theme'] = ok

    def _hotkey(self, hotkey_id):
        try:
            if hotkey_id in (1, 2):
                step_brightness(5 if hotkey_id == 1 else -5)
            elif hotkey_id == 3:
                dark = winapi.get_reg_dword(HKEY_CURRENT_USER, THEME_KEY, 'AppsUseLightTheme', 1) == 0
                apply_theme(not dark)
        except Exception:
            pass


# ---------------------------------------------------------------- main

def main():
    global SERVER_TOKEN, SERVER_PORT, BACKUP, GUI_HOST
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description='Desktop Console v3 backend host')
    parser.add_argument('--port', type=int, default=0, help='fixed port (default: random free port)')
    parser.add_argument('--no-window', action='store_true', help='run HTTP server only, no GUI')
    parser.add_argument('--selftest', action='store_true', help='run backend self test and exit')
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()

    if not winapi.acquire_single_instance(MUTEX_NAME):
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, '桌面控制台已经在运行了，请查看系统托盘。', APP_TITLE, 0)
        return 0

    try:
        BACKUP = Backup()
    except RuntimeError as exc:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(exc), APP_TITLE, 0x10)
        return 1

    STATE['topmost'] = False
    try:
        if os.path.exists(UI_STATE_FILE):
            with open(UI_STATE_FILE, encoding='utf-8-sig') as f:
                STATE['topmost'] = bool(json.load(f).get('topmost'))
    except Exception:
        pass

    SERVER_TOKEN = uuid.uuid4().hex
    server = ThreadingHTTPServer(('127.0.0.1', args.port if args.port > 0 else 0), Handler)
    server.daemon_threads = True
    SERVER_PORT = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, name='winctl-http', daemon=True)
    server_thread.start()

    refresh_monitors()

    gui = None
    if args.no_window:
        EXIT_EVENT.wait()
    else:
        # 目标机预检：WebView2 缺失时引导安装，而不是让 pywebview 静默失败
        if not webview2_runtime_installed():
            rc = ctypes.windll.user32.MessageBoxW(
                0, '本机缺少 WebView2 运行时，无法显示界面。\n\n'
                   'Windows 11 自带；Windows 10 需安装一次（装新版 Edge 也会自带）。\n\n'
                   '是否打开微软官方下载页？', APP_TITLE, 0x34)  # MB_ICONWARNING|MB_YESNO
            if rc == 6:  # IDYES
                ctypes.windll.shell32.ShellExecuteW(0, 'open', WEBVIEW2_DOWNLOAD_URL, None, None, 1)
            return 1
        gui = GuiHost()
        GUI_HOST = gui
        handle_api.gui = gui
        try:
            gui.run(SERVER_PORT)
        except Exception as exc:
            # GUI 启动失败时给出可见原因，而不是无声退出
            ctypes.windll.user32.MessageBoxW(
                0, '窗口启动失败：%s\n\n常见原因：\n'
                   '1) WebView2 运行时未安装（Win11 自带；Win10 可装 Edge 或 WebView2 Runtime）\n'
                   '2) .NET Framework 低于 4.6.2（Win10 1709+ / Win11 自带）' % exc,
                APP_TITLE, 0x10)

    if gui is not None and gui.ball is not None:
        try:
            gui.ball.stop()
        except Exception:
            pass
    if gui is not None and gui.tray is not None:
        try:
            gui.tray.stop()
        except Exception:
            pass
    EXIT_EVENT.set()
    try:
        server.shutdown()
    except Exception:
        pass
    for handle, _desc in monitor_list():
        winapi.destroy_monitor(handle)
    return 0


if __name__ == '__main__':
    sys.exit(main())

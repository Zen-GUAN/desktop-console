# -*- coding: utf-8 -*-
"""Build 桌面控制台.exe (route A: pywebview + PyInstaller, onefile + windowed).

Usage:  python build.py
Output: v3-exe/dist/桌面控制台.exe
"""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = '桌面控制台'
MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple'


def ensure_package(module_name, pip_name):
    if importlib.util.find_spec(module_name) is None:
        print('[build] installing %s ...' % pip_name)
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-i', MIRROR, pip_name])


def main():
    ensure_package('webview', 'pywebview')
    ensure_package('PyInstaller', 'pyinstaller')

    icon = os.path.join(HERE, 'icon.ico')
    if not os.path.exists(icon):
        subprocess.check_call([sys.executable, os.path.join(HERE, 'gen_icon.py')])

    ui_src = os.path.normpath(os.path.join(HERE, '..', 'v2-web', 'ui'))
    if not os.path.isdir(ui_src):
        raise SystemExit('ui source not found: %s' % ui_src)

    args = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', NAME,
        '--icon', icon,
        '--version-file', os.path.join(HERE, 'version_info.txt'),
        '--add-data', ui_src + os.pathsep + 'ui',
        '--add-data', icon + os.pathsep + '.',
        # pywebview 在 Windows 走 pythonnet/EdgeChromium，这几项保证运行时能找到 .NET 桥接
        '--collect-all', 'webview',
        '--collect-all', 'clr_loader',
        '--collect-all', 'pythonnet',
        os.path.join(HERE, 'app.py'),
    ]
    print('[build] ' + ' '.join(args))
    rc = subprocess.call(args, cwd=HERE)
    if rc != 0:
        raise SystemExit('[build] PyInstaller failed: %d' % rc)

    out = os.path.join(HERE, 'dist', NAME + '.exe')
    print('[build] OK: %s (%.1f MB)' % (out, os.path.getsize(out) / 1048576.0))


if __name__ == '__main__':
    main()

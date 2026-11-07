# -*- coding: utf-8 -*-
"""Headless acceptance test for the ball-settings page (docs/任务书_悬浮球设置页.md).

自起自停两个后端（v3 headless + v2 PowerShell），用系统 Edge 无头跑 UI 流程，全程无窗口。
Run:  python verify_ball_settings.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V3_PORT = 18801
V2_PORT = 18802
V3_BASE = 'http://127.0.0.1:%d' % V3_PORT
V2_BASE = 'http://127.0.0.1:%d' % V2_PORT
DEFAULT_FUNCS = ['panel', 'theme', 'bright_up', 'bright_down', 'restore']

_results = []


def check(name, ok, detail=''):
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))
    _results.append(ok)


def http_json(base, path, token=None, body=None):
    """直连（绕过一切代理环境变量）的极简 API 客户端。"""
    headers = {}
    if token:
        headers['X-WinCtl-Token'] = token
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(base + path, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as resp:
        return json.load(resp)


def wait_ready(base, timeout=25):
    """任何 HTTP 应答（含 403/404）都算就绪；只有连不上才继续等。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json(base, '/api/state', token='x')
            return True
        except urllib.error.HTTPError:
            return True  # 服务在应答，只是 token 不对
        except Exception:
            time.sleep(0.4)
    return False


def kill(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def wait_expr(page, expr, timeout=10):
    """轮询 evaluate（CSP 禁 eval，不能用 wait_for_function 字符串）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.evaluate(expr):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def main():
    from playwright.sync_api import sync_playwright

    v3 = subprocess.Popen([sys.executable, os.path.join(HERE, 'app.py'),
                           '--no-window', '--port', str(V3_PORT)],
                          cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    v2 = subprocess.Popen(['powershell', '-ExecutionPolicy', 'Bypass', '-File',
                           os.path.join(ROOT, 'v2-web', 'server', 'Server.ps1'),
                           '-NoBrowser', '-Port', str(V2_PORT)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    page = None
    browser = None
    page_errors = []
    try:
        check('v3 后端就绪', wait_ready(V3_BASE))
        check('v2 后端就绪', wait_ready(V2_BASE))

        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 380, 'height': 600})

            # ---------- v3：入口可见 + 完整交互 ----------
            page = context.new_page()
            page.on('pageerror', lambda e: page_errors.append('PAGEERROR: %s' % e))
            page.on('console', lambda m: page_errors.append('CONSOLE[%s]: %s' % (m.type, m.text))
                    if m.type == 'error' and 'inline style' not in m.text else None)
            page.goto(V3_BASE + '/')
            try:
                # 入口在未打开的 dialog 内（包围盒为空），不能按"可见"等；等 attached + 属性解封
                page.wait_for_selector('#ball-settings-button', state='attached', timeout=10000)
                ok = wait_expr(page, "document.querySelector('#ball-settings-button').hidden === false")
            except Exception:
                # 失败时抓现场：状态条/连接指示/控制台错误，便于定位
                time.sleep(1)
                diag = {
                    'statusbar': page.text_content('#status-text'),
                    'pill': page.text_content('#local-pill'),
                    'hidden': page.eval_on_selector('#ball-settings-button', 'e=>e.hidden'),
                }
                check('v3 入口显示（state.ball 能力检测）', False,
                      '%s | %s' % (diag, page_errors[:3]))
                raise
            page_errors.clear()
            check('v3 入口显示（state.ball 能力检测）', ok)

            token = page.evaluate("document.querySelector('meta[name=winctl-token]').content")

            def cfg():
                return http_json(V3_BASE, '/api/ball/config', token=token)

            page.click('#advanced-button')
            page.click('#ball-settings-button')
            page.wait_for_selector('#ball-dialog[open]')
            check('设置页打开且渲染 9 项功能',
                  wait_expr(page,
                            "document.querySelectorAll('#ball-function-list input[type=checkbox]').length === 9",
                            timeout=8))

            labels = page.eval_on_selector_all(
                '#ball-function-list .ball-check-label', 'els => els.map(e => e.textContent)')
            check('label 全部来自后端 available',
                  labels == [i['label'] for i in cfg()['available']], str(labels[:3]) + '...')

            preview = page.eval_on_selector_all('#ball-preview-list li', 'els => els.map(e => e.textContent)')
            check('默认预览 5 项（默认清单顺序）', len(preview) == 5, str(preview))

            # 勾选「更换壁纸」→ 追加到末尾，即时保存
            page.click('#ball-function-list label.ball-check:has-text("更换壁纸")')
            page.wait_for_timeout(600)
            data = cfg()
            check('勾选即时保存（追加末尾）',
                  data['functions'][-1] == 'wallpaper' and len(data['functions']) == 6,
                  str(data['functions']))

            # 上移一次 → 倒数第二
            page.click('#ball-function-list .ball-function:has-text("更换壁纸") .ball-order-button.up')
            page.wait_for_timeout(600)
            data = cfg()
            check('上移即时保存（顺序生效）',
                  len(data['functions']) >= 2 and data['functions'][-2] == 'wallpaper',
                  str(data['functions']))

            # 预览与序号同步
            preview = page.eval_on_selector_all('#ball-preview-list li', 'els => els.map(e => e.textContent)')
            check('球菜单预览与设置同步', len(preview) == len(data['functions'])
                  and preview[-2] == '更换壁纸', str(preview))

            # 开关：关 → enabled=false；开 → 恢复
            page.click('#ball-enabled')
            page.wait_for_timeout(600)
            data = cfg()
            off_ok = data['enabled'] is False
            page.click('#ball-enabled')
            page.wait_for_timeout(600)
            data = cfg()
            check('启用开关即时保存', off_ok and data['enabled'] is True, str(data['enabled']))

            # 恢复默认清单，给后续断连测试一个干净基线
            http_json(V3_BASE, '/api/ball/config', token=token,
                      body={'enabled': True, 'functions': DEFAULT_FUNCS})
            page.reload()
            page.wait_for_selector('#ball-settings-button', state='attached', timeout=8000)
            wait_expr(page, "document.querySelector('#ball-settings-button').hidden === false")
            check('重载后基线恢复', cfg()['functions'] == DEFAULT_FUNCS)

            # ---------- 断连回滚：先开设置页并加载完配置，再杀 v3，触发"保存失败"路径 ----------
            page.click('#advanced-button')
            page.click('#ball-settings-button')
            page.wait_for_selector('#ball-dialog[open]')
            wait_expr(page,
                      "document.querySelectorAll('#ball-function-list input[type=checkbox]').length === 9",
                      timeout=8)
            kill(v3)
            time.sleep(1)
            page.click('#ball-function-list label.ball-check:has-text("更换壁纸")')
            page.wait_for_selector('#ball-save-error:not([hidden])', timeout=10000)
            err = page.text_content('#ball-save-error')
            check('断连保存失败 → 弹窗内错误条 + 回滚', '保存失败' in (err or ''), (err or '')[:40])
            reverted = page.eval_on_selector_all(
                '#ball-function-list input[type=checkbox]', 'els => els.map(e => e.checked)')
            check('UI 回滚到服务器真值（wallpaper 未勾上）',
                  reverted[5] is False, str(reverted))

            # ---------- v2：入口隐藏 + 零 ball 请求 ----------
            ball_requests = []
            page2 = context.new_page()
            page2.on('request', lambda r: ball_requests.append(r.url) if '/api/ball/' in r.url else None)
            page2.goto(V2_BASE + '/')
            page2.wait_for_selector('#local-pill', timeout=8000)
            page2.wait_for_timeout(1200)  # 等 renderState 跑完
            hidden = page2.eval_on_selector('#ball-settings-button', 'el => el.hidden')
            check('v2 入口隐藏（能力检测）', hidden is True)
            page2.click('#advanced-button')
            page2.wait_for_timeout(400)
            check('v2 全程零 ball API 请求', len(ball_requests) == 0, str(ball_requests[:2]))
            page2.close()
            page.close()

            # ---------- v2 自身功能回归（退出端点走一遍） ----------
            try:
                html = urllib.request.build_opener(urllib.request.ProxyHandler({})) \
                    .open(V2_BASE + '/', timeout=5).read().decode('utf-8')
                import re
                m = re.search(r'content="([0-9a-f]{32})"', html)
                http_json(V2_BASE, '/api/exit', token=m.group(1), body={})
                check('v2 退出端点正常', True)
            except Exception as exc:
                check('v2 退出端点正常', False, str(exc))
    finally:
        # 先杀后端再关浏览器（浏览器 close 偶发 event-loop 异常，不能挡住清理）
        kill(v3)
        kill(v2)
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    failed = [r for r in _results if not r]
    print('verify: %d/%d passed' % (len(_results) - len(failed), len(_results)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

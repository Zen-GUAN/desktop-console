# -*- coding: utf-8 -*-
"""Headless layout acceptance for the layout-adaptation task (docs/任务书_布局适配.md).

三档视口几何断言（340×480 / 1200×900 / 380×600），playwright + 系统 Edge，全程无窗口。
Run:  python verify_layout.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 18810
BASE = 'http://127.0.0.1:%d' % PORT

_results = []


def check(name, ok, detail=''):
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))
    _results.append(ok)


def wait_ready(base, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})) \
                .open(base + '/api/state', timeout=3).read()
            return True
        except urllib.error.HTTPError:
            return True
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


MEASURE = """() => {
  const sb = document.querySelector('#statusbar').getBoundingClientRect();
  const c = document.querySelector('.content');
  const ua = document.querySelector('.under-actions').getBoundingClientRect();
  const card = document.querySelector('.brightness-card').getBoundingClientRect();
  return {
    vh: window.innerHeight, vw: window.innerWidth,
    scrollW: document.documentElement.scrollWidth,
    sbBottom: sb.bottom, sbTop: sb.top,
    sbMargin: parseFloat(getComputedStyle(document.querySelector('#statusbar')).marginBottom),
    contentScrollH: c.scrollHeight, contentClientH: c.clientHeight,
    contentBottom: c.getBoundingClientRect().bottom,
    uaBottom: ua.bottom, uaTop: ua.top,
    cardW: card.width, cardLeft: card.left, cardRight: card.right
  };
}"""


def main():
    from playwright.sync_api import sync_playwright

    v3 = subprocess.Popen([sys.executable, os.path.join(HERE, 'app.py'),
                           '--no-window', '--port', str(PORT)],
                          cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = None
    try:
        check('v3 后端就绪', wait_ready(BASE))
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 380, 'height': 600})

            def load(w, h):
                page = context.new_page()
                page.set_viewport_size({'width': w, 'height': h})
                page.goto(BASE + '/')
                page.wait_for_selector('#statusbar', state='attached', timeout=10000)
                page.wait_for_timeout(600)  # 等 renderState
                return page

            # ---------- 340×480：底栏完整 + 下边距 + 可滚动到底 ----------
            page = load(340, 480)
            m = page.evaluate(MEASURE)
            check('340: 状态栏在视口内', m['sbBottom'] <= m['vh'] + 1, 'sbBottom=%.1f vh=%d' % (m['sbBottom'], m['vh']))
            check('340: 状态栏下边距 ≥8px', m['sbMargin'] >= 8, 'margin=%.1f' % m['sbMargin'])
            check('340: 无水平溢出', m['scrollW'] <= 341, 'scrollW=%d' % m['scrollW'])
            scrollable = m['contentScrollH'] >= m['contentClientH'] - 1
            page.eval_on_selector('.content', 'el => { el.scrollTop = el.scrollHeight; }')
            page.wait_for_timeout(200)
            m2 = page.evaluate(MEASURE)
            check('340: 内容可滚动且「更多工具」完整可见',
                  (not scrollable) or (m2['uaBottom'] <= m2['contentBottom'] + 2 and m2['uaTop'] >= 0),
                  'scrollable=%s uaBottom=%.1f contentBottom=%.1f' % (scrollable, m2['uaBottom'], m2['contentBottom']))
            page.close()

            # ---------- 1200×900：卡片限宽 460 居中 ----------
            page = load(1200, 900)
            m = page.evaluate(MEASURE)
            check('1200: 主体限宽 460px', abs(m['cardW'] - 460) <= 1.5, 'cardW=%.1f' % m['cardW'])
            check('1200: 水平居中', abs(m['cardLeft'] - (m['vw'] - m['cardRight'])) <= 2,
                  'left=%.1f right_gap=%.1f' % (m['cardLeft'], m['vw'] - m['cardRight']))
            check('1200: 无水平溢出', m['scrollW'] <= 1201, 'scrollW=%d' % m['scrollW'])
            page.close()

            # ---------- 380×600：默认形态不劣化 ----------
            page = load(380, 600)
            m = page.evaluate(MEASURE)
            check('380: 状态栏在视口内且有下边距', m['sbBottom'] <= m['vh'] + 1 and m['sbMargin'] >= 8,
                  'sbBottom=%.1f margin=%.1f' % (m['sbBottom'], m['sbMargin']))
            check('380: 无水平溢出', m['scrollW'] <= 381, 'scrollW=%d' % m['scrollW'])
            check('380: 卡片未超宽', m['cardW'] <= 380, 'cardW=%.1f' % m['cardW'])
            page.close()

            # ---------- CSP 告警（排除既有 inline-style 噪音） ----------
            page = context.new_page()
            noise = []
            page.on('console', lambda msg: noise.append(msg.text)
                    if msg.type == 'error' and 'inline style' not in msg.text else None)
            page.goto(BASE + '/')
            page.wait_for_timeout(1500)
            check('无新增 CSP/JS 告警', len(noise) == 0, str(noise[:2]))
            page.close()
    finally:
        kill(v3)
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    failed = _results.count(False)
    print('verify: %d/%d passed' % (len(_results) - failed, len(_results)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

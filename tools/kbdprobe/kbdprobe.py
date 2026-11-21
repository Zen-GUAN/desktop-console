#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kbdprobe —— 键盘延迟探针（Windows 专用，仅 Python 标准库）

用途：把「键盘迟滞 / 粘滞」的主观感觉变成可复现的量化数据（分位数 + 异常计数）。

隐私红线（务必保持，改代码前重读）：
  本脚本只允许记录四类信息——
    1) VK 虚拟键码（纯数字，如 65）
    2) 时间戳（高精度单调钟 perf_counter_ns）
    3) 事件类型（down / repeat / up）
    4) 派生指标（按住时长、相邻间隔、处理延迟）
  绝不记录、绝不映射按键字符或键名，绝不读取窗口标题或输入法内容。
  源码中不存在 GetKeyNameText / ToUnicode / ToAscii / GetWindowText / 剪贴板
  等任何调用，可用 grep 自行验证（见 README「隐私自审」）。

原理：
  SetWindowsHookExW(WH_KEYBOARD_LL) 低级键盘钩子 + 消息泵打点。
  不注入、不改键、不拦截：回调一律 CallNextHookEx 原样放行（含异常路径）。
  无需管理员权限。

用法：
  python kbdprobe.py --seconds 60        # 采 60 秒，到点自动出报告
  python kbdprobe.py                     # 采到 Ctrl+C 为止
  python kbdprobe.py --selftest          # 合成数据自测（不碰真实键盘，无文件写出）
  python kbdprobe.py --out D:\\somewhere  # 指定输出目录（默认 <脚本目录>\\samples）
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

# ---- 判读阈值（口径详见 README；改这里 = 改判读标准） ----
STALL_GAP_MS = 200.0      # 相邻间隔超过它 → 记为「卡顿点」
STICKY_HOLD_MS = 300.0    # 按住超过它 → 疑似粘滞（还须无自动重复伴随才计「异常」）
PROC_LAG_MS = 200.0       # 单次处理延迟超过它 → 明显迟滞证据
SCHED_OVER_MS = 100.0     # 调度延迟（200ms 睡眠的超时部分）超过它 → 系统负载信号
SCHED_PROBE_S = 0.2       # 调度探针周期
FLUSH_S = 5.0             # 采样中途临时落盘周期（崩溃保护）

CSV_HEADER = ["t_ns", "sys_event_ms", "vk", "kind", "hold_ms", "gap_ms", "proc_ms"]
KIND_TOKENS = ("down", "repeat", "up")


# ============================================================
# 基础统计
# ============================================================

def pct(values, q):
    """线性插值分位数（numpy 'linear' 口径）。空表返回 None。"""
    if not values:
        return None
    s = sorted(float(v) for v in values)
    if len(s) == 1:
        return s[0]
    idx = q * (len(s) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def stats4(values):
    """p50 / p95 / p99 / max + 样本数。"""
    return {
        "n": len(values),
        "p50": pct(values, 0.50),
        "p95": pct(values, 0.95),
        "p99": pct(values, 0.99),
        "max": (max(values) if values else None),
    }


def fmt(x, nd=1):
    return "无数据" if x is None else f"{x:.{nd}f}"


# ============================================================
# 事件衍生（唯一状态机：采样落盘与自测共用）
# ============================================================

def derive(raw_events):
    """把原始事件流补齐 kind / hold_ms / gap_ms。

    raw: [(t_ns, vk, wkind, sys_ms, proc_ms), ...]
      wkind ∈ {"down","up"}（WM_KEYDOWN/WM_SYSKEYDOWN 归 down，KEYUP/SYSKEYUP 归 up）
    返回行 [t_ns, sys_event_ms, vk, kind, hold_ms, gap_ms, proc_ms]：
      kind   down=首次按下；repeat=同键未抬起又来的 down（系统自动重复）；up=抬起
      hold_ms up 与其配对 down 的毫秒差（无配对为 None，如会话开始前就按着的键）
      gap_ms 与上一条事件的毫秒差
    幂等：可对同一批原始事件重复调用（中途落盘用）。"""
    rows = []
    pending = {}
    prev_t = None
    for t_ns, vk, wkind, sys_ms, proc_ms in raw_events:
        hold = None
        if wkind == "up":
            t0 = pending.pop(vk, None)
            hold = None if t0 is None else (t_ns - t0) / 1e6
            kind = "up"
        else:
            if vk in pending:
                kind = "repeat"  # 自动重复：保留首次按下时间，不覆盖
            else:
                pending[vk] = t_ns
                kind = "down"
        gap = None if prev_t is None else (t_ns - prev_t) / 1e6
        prev_t = t_ns
        rows.append([t_ns, sys_ms, vk, kind, hold, gap, proc_ms])
    return rows


def _is_os_repeat_gap(rows, i):
    """down→repeat 的首个间隔 = 系统自动重复延迟（默认约 250~500ms，是 OS 配置
    不是卡顿），统计相邻间隔/卡顿点时剔除。repeat→repeat、repeat→up 保留
    （自动重复流中断反而是系统卡顿证据）。"""
    return i > 0 and rows[i][3] == "repeat" and rows[i - 1][3] == "down"


def wall_str(meta, t_ns):
    """把 perf_counter 时基换算成本地墙上时间 HH:MM:SS.mmm（只有时间，无键名）。"""
    try:
        wall = meta["wall0"] + (t_ns - meta["perf0"]) / 1e9
        return datetime.fromtimestamp(wall).strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return "?"


# ============================================================
# 分析
# ============================================================

def analyze(rows, sched_over_ms, meta):
    """对衍生行 + 调度探针样本做全套统计，返回分析 dict（render_report 消费）。"""
    holds = [r[4] for r in rows if r[4] is not None]
    gaps = [r[5] for i, r in enumerate(rows)
            if r[5] is not None and not _is_os_repeat_gap(rows, i)]
    procs = [r[6] for r in rows if r[6] is not None]

    # 卡顿点：有效相邻间隔 > 阈值（时间戳 + 间隔大小 + VK 数字，无键名）
    stalls = []
    for i, r in enumerate(rows):
        g = r[5]
        if g is not None and g > STALL_GAP_MS and not _is_os_repeat_gap(rows, i):
            stalls.append({"wall": wall_str(meta, r[0]), "gap_ms": round(g, 1),
                           "vk": r[2]})

    # 处理延迟超阈值明细
    proc_over_list = [[wall_str(meta, r[0]), round(r[6], 1)]
                      for r in rows if r[6] is not None and r[6] > PROC_LAG_MS]

    # 粘滞判定：按住超阈值且按住期间自动重复一次都没来。
    # 依据：正常长按（>重复延迟）必然伴随 ~30ms 间隔的 repeat 流；
    # repeat 没来而 up 迟到 → 系统侧队列/驱动卡住，才是「粘滞」的机器证据。
    pending = {}
    sticky_susp = []
    for i, r in enumerate(rows):
        k = r[3]
        if k == "down":
            pending[r[2]] = [i, 0]
        elif k == "repeat":
            p = pending.get(r[2])
            if p is not None:
                p[1] += 1
        elif k == "up" and r[4] is not None and r[4] > STICKY_HOLD_MS:
            p = pending.pop(r[2], None)
            if p is not None and p[1] == 0:
                sticky_susp.append({"wall": wall_str(meta, r[0]),
                                    "hold_ms": round(r[4], 1), "vk": r[2]})

    proc_over_n = len(proc_over_list)
    hold_over_n = sum(1 for h in holds if h > STICKY_HOLD_MS)
    sched_over_n = sum(1 for s in sched_over_ms if s > SCHED_OVER_MS)

    verdicts = []
    if proc_over_n:
        verdicts.append(f"疑似迟滞：处理延迟单次 >{PROC_LAG_MS:.0f}ms 共 {proc_over_n} 次")
    if sticky_susp:
        verdicts.append(f"疑似粘滞：按住 >{STICKY_HOLD_MS:.0f}ms 且无自动重复伴随 共 {len(sticky_susp)} 次")
    if not verdicts:
        verdicts.append("未发现超阈值的迟滞 / 粘滞量化证据")

    return {
        "n_rows": len(rows),
        "n_down": sum(1 for r in rows if r[3] == "down"),
        "n_repeat": sum(1 for r in rows if r[3] == "repeat"),
        "n_up": sum(1 for r in rows if r[3] == "up"),
        "unique_vk": len({r[2] for r in rows}),
        "hold": stats4(holds),
        "gap": stats4(gaps),
        "proc": stats4(procs),
        "sched": stats4(sched_over_ms),
        "stalls": stalls,
        "proc_over_n": proc_over_n,
        "proc_over_list": proc_over_list,
        "hold_over_n": hold_over_n,
        "sticky_susp": sticky_susp,
        "sched_over_n": sched_over_n,
        "verdicts": verdicts,
    }


def render_report(a, meta):
    """渲染文本报告（写入 _report.txt 并打印）。只含数字/时间戳/VK 数字。"""
    L = []
    bar = "=" * 60
    L.append(bar)
    L.append("键盘延迟探针 · 采样报告（kbdprobe）")
    L.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(bar)
    L.append("")
    L.append("[会话]")
    L.append(f"  采样时长: {meta.get('duration_s', 0):.1f} s（请求 {meta.get('requested_s')}）")
    L.append(f"  按键事件数: {a['n_rows']}（down {a['n_down']} / repeat {a['n_repeat']} / up {a['n_up']}）")
    L.append(f"  独立按键数(不同VK): {a['unique_vk']}")
    L.append(f"  主机: {meta.get('host', '?')}")
    L.append(f"  输出: {meta.get('csv_path', '-')} / {meta.get('rep_path', '-')}")
    L.append("")

    h, g, p, s = a["hold"], a["gap"], a["proc"], a["sched"]
    L.append(f"[按住时长 hold_ms]  n={h['n']}")
    L.append(f"  p50={fmt(h['p50'])}  p95={fmt(h['p95'])}  p99={fmt(h['p99'])}  max={fmt(h['max'])}")
    L.append("")
    L.append("[相邻间隔 gap_ms]（已剔除 down→repeat 的系统自动重复延迟）")
    L.append(f"  n={g['n']}  p50={fmt(g['p50'])}  p95={fmt(g['p95'])}  p99={fmt(g['p99'])}  max={fmt(g['max'])}")
    L.append(f"  >{STALL_GAP_MS:.0f}ms 卡顿点 {len(a['stalls'])} 个（时间戳 | 间隔 | 当前键VK数字）:")
    if a["stalls"]:
        for st in a["stalls"]:
            L.append(f"    {st['wall']}  gap={st['gap_ms']}ms  vk={st['vk']}")
    else:
        L.append("    （无）")
    L.append("")
    L.append(f"[处理延迟 proc_ms]（系统事件时间戳→本进程处理；含 ±8ms 时钟粒度噪声）")
    L.append(f"  n={p['n']}  p50={fmt(p['p50'])}  p95={fmt(p['p95'])}  max={fmt(p['max'])}")
    L.append(f"  >{PROC_LAG_MS:.0f}ms 次数: {a['proc_over_n']}")
    if a["proc_over_list"]:
        for w, v in a["proc_over_list"]:
            L.append(f"    {w}  proc={v}ms")
    L.append("")
    L.append(f"[调度延迟 sched_ms]（每 200ms 睡眠一次的实际超时部分）")
    L.append(f"  n={s['n']}  p50={fmt(s['p50'])}  p95={fmt(s['p95'])}  max={fmt(s['max'])}")
    L.append(f"  >{SCHED_OVER_MS:.0f}ms 次数: {a['sched_over_n']}")
    L.append("")

    L.append("[结论]")
    L.append(f"  处理延迟>200ms: {'有（' + str(a['proc_over_n']) + ' 次）' if a['proc_over_n'] else '无'}")
    L.append(f"  异常按住(>300ms 且无自动重复伴随): {'有（' + str(len(a['sticky_susp'])) + ' 次）' if a['sticky_susp'] else '无'}"
             f"（含故意长按共 {a['hold_over_n']} 次）")
    L.append(f"  卡顿点(gap>200ms): {len(a['stalls'])} 个（判读先看 README：结合处理延迟区分人停顿/机器卡）")
    for v in a["verdicts"]:
        L.append(f"  ▶ {v}")
    L.append("")

    snap = meta.get("snapshot_start"), meta.get("snapshot_end")
    if any(snap):
        L.append("[系统快照]")
        L.append(f"  开始: {json.dumps(snap[0], ensure_ascii=False, default=str)}")
        L.append(f"  结束: {json.dumps(snap[1], ensure_ascii=False, default=str)}")
        L.append("")
    L.append("[隐私声明]")
    L.append("  本日志仅包含 VK 数字、时间戳、事件类型与派生指标；")
    L.append("  不含按键字符/键名/窗口标题/输入法内容（源码可 grep 验证）。")
    L.append(bar)
    return "\n".join(L)


# ============================================================
# 落盘（原子写：临时文件 + os.replace）
# ============================================================

def csv_text(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for r in rows:
        w.writerow([r[0], r[1], r[2], r[3],
                    "" if r[4] is None else f"{r[4]:.3f}",
                    "" if r[5] is None else f"{r[5]:.3f}",
                    "" if r[6] is None else f"{r[6]:.3f}"])
    return buf.getvalue()


def write_text_atomic(path, text, finalize=True):
    """finalize=False 时只写临时文件（采样中途的崩溃保护），不替换目标。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if finalize:
        os.replace(tmp, path)


def _run_powercfg(args_list):
    """powercfg 子进程：输出编码随控制台 CP 变化（管道下 chcp 65001 → UTF-8，
    默认 → GBK/ANSI），按字节流收，utf-8 失败再退 mbcs。"""
    try:
        r = subprocess.run(args_list, capture_output=True, timeout=10)
        for enc in ("utf-8", "mbcs", "latin-1"):
            try:
                return r.stdout.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return ""
    except Exception:
        return ""


# ============================================================
# 系统侧快照（尽力而为，逐项失败置 None，不影响采样）
# ============================================================

def snapshot_system():
    out = {
        "cpu_busy_pct": None, "dpc_pct": None, "proc_queue_len": None,
        "avail_mem_mb": None, "mem_load_pct": None,
        "power_plan": None, "usb_selective_suspend": None,
    }
    if sys.platform != "win32":
        return out
    try:
        import ctypes as ct
        k32 = ct.WinDLL("kernel32")

        class FT64(ct.Structure):
            _fields_ = [("v", ct.c_uint64)]

        def _times():
            i, k, u = FT64(), FT64(), FT64()
            if k32.GetSystemTimes(ct.byref(i), ct.byref(k), ct.byref(u)):
                return i.v, k.v, u.v
            return None

        class MEMORYSTATUSEX(ct.Structure):
            _fields_ = [("dwLength", ct.c_uint32), ("dwMemoryLoad", ct.c_uint32),
                        ("ullTotalPhys", ct.c_uint64), ("ullAvailPhys", ct.c_uint64),
                        ("ullTotalPageFile", ct.c_uint64), ("ullAvailPageFile", ct.c_uint64),
                        ("ullTotalVirtual", ct.c_uint64), ("ullAvailVirtual", ct.c_uint64),
                        ("ullAvailExtendedVirtual", ct.c_uint64)]

        m = MEMORYSTATUSEX()
        m.dwLength = ct.sizeof(MEMORYSTATUSEX)
        if k32.GlobalMemoryStatusEx(ct.byref(m)):
            out["avail_mem_mb"] = round(m.ullAvailPhys / 1048576, 1)
            out["mem_load_pct"] = m.dwMemoryLoad

        t1 = _times()
        # PDH：处理器队列长度 + DPC 时间%（0.25s 双采差分）
        try:
            pdh = ct.WinDLL("pdh")
            PDH_FMT_DOUBLE = 0x00000200

            class CV(ct.Structure):
                _fields_ = [("CStatus", ct.c_uint32), ("pad", ct.c_uint32),
                            ("d", ct.c_double)]

            hq, hc1, hc2 = ct.c_void_p(), ct.c_void_p(), ct.c_void_p()
            if pdh.PdhOpenQueryW(None, 0, ct.byref(hq)) == 0:
                ok1 = pdh.PdhAddEnglishCounterW(
                    hq, "\\System\\Processor Queue Length", 0, ct.byref(hc1)) == 0
                ok2 = pdh.PdhAddEnglishCounterW(
                    hq, "\\Processor(_Total)\\% DPC Time", 0, ct.byref(hc2)) == 0
                pdh.PdhCollectQueryData(hq)
                time.sleep(0.25)
                pdh.PdhCollectQueryData(hq)
                v1, v2 = CV(), CV()
                if ok1 and pdh.PdhGetFormattedCounterValue(
                        hc1, PDH_FMT_DOUBLE, None, ct.byref(v1)) == 0:
                    out["proc_queue_len"] = round(v1.d, 1)
                if ok2 and pdh.PdhGetFormattedCounterValue(
                        hc2, PDH_FMT_DOUBLE, None, ct.byref(v2)) == 0:
                    out["dpc_pct"] = round(v2.d, 2)
                pdh.PdhCloseQuery(hq)
        except Exception:
            pass
        t2 = _times()
        if t1 and t2 and (t2[1] - t1[1]) > 0:
            out["cpu_busy_pct"] = round(100.0 * (1.0 - (t2[0] - t1[0]) / (t2[1] - t1[1])), 1)
    except Exception:
        pass

    # 电源计划（只读，不改任何设置）
    try:
        txt = _run_powercfg(["powercfg", "/getactivescheme"])
        m2 = re.search(r"GUID:\s*([0-9a-fA-F-]{36})\s*(?:\((.*)\))?", txt)
        out["power_plan"] = {
            "guid": m2.group(1) if m2 else None,
            "name": (m2.group(2) or "").strip() if m2 else (txt.strip()[:80] or None),
        }
    except Exception:
        pass
    # USB 选择性暂停当前值（SUB_USB 子组 2a737441-…；按名称找设置块，只读）
    try:
        txt = _run_powercfg(
            ["powercfg", "/q", "SCHEME_CURRENT",
             "2a737441-1930-4402-8d77-b2bebba308a3"])
        val, name = None, None
        for block in re.split(r"(?:电源设置|Power Setting) GUID[：:]", txt)[1:]:
            m3 = re.search(r"\((.*?)\)", block)
            name = m3.group(1).strip() if m3 else ""
            if ("选择性暂停" in name) or ("Selective" in name):
                ac = re.search(r"(?:交流|AC)[^\r\n]*?(0x[0-9a-fA-F]+)", block)
                dc = re.search(r"(?:直流|DC)[^\r\n]*?(0x[0-9a-fA-F]+)", block)
                if ac:
                    val = int(ac.group(1), 16)
                elif dc:
                    val = int(dc.group(1), 16)
                break
        out["usb_selective_suspend"] = {
            "setting": name or None, "value": val,
            "meaning": {0: "禁用（键鼠建议）", 1: "启用（延迟敏感设备建议改禁用）"}.get(val),
        }
    except Exception:
        pass
    return out


# ============================================================
# 采样主流程（Windows 专用）
# ============================================================

def _calibrate_clock(k32, samples=24):
    """把 KBDLLHOOKSTRUCT.time（GetTickCount 时基，粒度 ~15.6ms）对齐到
    perf_counter 时基：offset = tick64_ms*1e6 - perf_ns 的中位数。
    残差 ≈ ±8ms 粒度噪声，判读时忽略 10ms 级差异（README 已注明）。"""
    vals = []
    for i in range(samples):
        tick = k32.GetTickCount64()
        p = time.perf_counter_ns()
        vals.append(tick * 1_000_000 - p)
        time.sleep(0.002 + (i % 5) * 0.004)  # 打散相位，覆盖 tick 粒度
    vals.sort()
    return vals[len(vals) // 2]


def run_sampling(args):
    if sys.platform != "win32":
        print("[错误] 采样仅支持 Windows；统计/自测可用 --selftest（跨平台）。")
        return 4

    import ctypes as ct
    from ctypes import wintypes

    user32 = ct.WinDLL("user32", use_last_error=True)
    k32 = ct.WinDLL("kernel32", use_last_error=True)

    WH_KEYBOARD_LL = 13
    WM_KEYUP, WM_SYSKEYUP = 0x0101, 0x0105
    QS_ALLINPUT = 0x04FF
    PM_REMOVE = 1

    class KBDLLHOOKSTRUCT(ct.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ct.c_size_t)]

    class MSG(ct.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                    ("lPrivate", wintypes.DWORD)]

    HOOKPROC = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)
    # ---- 显式签名：LL 钩子回调里的 l_param 是 64 位指针值，缺省按 c_int 转换会在
    # ---- 高位为 1 时抛 OverflowError → 回调返回 0 = 拦截按键（红线）。必须钉死签名。
    user32.CallNextHookEx.restype = ct.c_ssize_t
    user32.CallNextHookEx.argtypes = [
        ct.c_void_p, ct.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowsHookExW.restype = ct.c_void_p
    user32.SetWindowsHookExW.argtypes = [
        wintypes.DWORD, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.UnhookWindowsHookEx.argtypes = [ct.c_void_p]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ct.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.DispatchMessageW.restype = ct.c_ssize_t
    k32.GetTickCount64.restype = ct.c_ulonglong  # 缺省 c_int 会在开机 >24.8 天后截断
    user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    user32.MsgWaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ct.POINTER(wintypes.HANDLE), wintypes.BOOL,
        wintypes.DWORD, wintypes.DWORD]

    offset_ns = _calibrate_clock(k32)
    raw = []            # [(t_ns, vk, wkind, sys_ms, proc_ms)]
    sched_over = []     # 调度探针：200ms 睡眠实际超时（ms）
    stop = threading.Event()

    def on_key(n_code, w_param, l_param):
        # 红线：任何情况下都原样放行（CallNextHookEx），打点失败也绝不拦截按键
        try:
            if n_code >= 0:
                t_ns = time.perf_counter_ns()
                k = ct.cast(l_param, ct.POINTER(KBDLLHOOKSTRUCT)).contents
                wkind = "up" if w_param in (WM_KEYUP, WM_SYSKEYUP) else "down"
                proc_ms = (t_ns - (k.time * 1_000_000 - offset_ns)) / 1e6
                if proc_ms < 0:  # 极罕见：tick 回绕 / 时钟噪声
                    proc_ms = 0.0
                raw.append((t_ns, k.vkCode, wkind, k.time, proc_ms))
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    hook_ref = HOOKPROC(on_key)  # 持引用防 GC（防钩子静默失效）
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_ref, None, 0)
    if not hook:
        err = ct.get_last_error()
        print(f"[错误] 安装低级键盘钩子失败 (GetLastError={err})。")
        print("  常见原因：安全软件拦截 LL 钩子。现象与替代方案（Raw Input）见 README。")
        return 3

    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, f"kbdprobe_{stamp}.csv")
    rep_path = os.path.join(out_dir, f"kbdprobe_{stamp}_report.txt")

    def flush_tmp():
        write_text_atomic(csv_path, csv_text(derive(raw)), finalize=False)

    def sched_probe():
        while not stop.is_set():
            t0 = time.perf_counter_ns()
            woken = stop.wait(SCHED_PROBE_S)
            t1 = time.perf_counter_ns()
            if woken:
                break
            sched_over.append(max(0.0, (t1 - t0) / 1e6 - SCHED_PROBE_S * 1000.0))

    winver = sys.getwindowsversion()
    host = (f"Windows {winver.major}.{winver.minor} build {winver.build}, "
            f"Python {platform.python_version()}, CPU×{os.cpu_count()}")

    requested_s = args.seconds
    duration_hint = f"{requested_s:.0f}s" if requested_s else "Ctrl+C 手动停止"
    print("=" * 60)
    print(f"键盘延迟探针 · 采样中（{duration_hint}）")
    print("隐私：只记录 VK 数字/时间戳/事件类型，绝不记录按键内容。")
    print("请正常打字 / 游戏；结束时自动生成 CSV + 报告。")
    print("=" * 60)

    snap_start = snapshot_system()
    perf0 = time.perf_counter_ns()
    wall0 = time.time()
    deadline = perf0 + int(requested_s * 1e9) if requested_s else None
    next_flush = time.perf_counter() + FLUSH_S

    probe = threading.Thread(target=sched_probe, daemon=True)
    probe.start()
    interrupted = False
    try:
        while True:
            if deadline is not None and time.perf_counter_ns() >= deadline:
                break
            user32.MsgWaitForMultipleObjects(0, None, 0, 50, QS_ALLINPUT)
            while True:  # 泵干当前消息（钩子回调在此期间被系统调用）
                msg = MSG()
                if not user32.PeekMessageW(ct.byref(msg), None, 0, 0, PM_REMOVE):
                    break
                user32.TranslateMessage(ct.byref(msg))
                user32.DispatchMessageW(ct.byref(msg))
            now = time.perf_counter()
            if now >= next_flush:  # 周期落盘：异常退出也不丢已采数据
                flush_tmp()
                el = (time.perf_counter_ns() - perf0) / 1e9
                print(f"  … 已采 {len(raw)} 条事件 / {el:.0f}s", flush=True)
                next_flush = now + FLUSH_S
    except KeyboardInterrupt:
        interrupted = True
        print("\n[中断] 收到 Ctrl+C，保存已采数据…")
    finally:
        stop.set()
        probe.join(timeout=1.0)
        perf1 = time.perf_counter_ns()
        user32.UnhookWindowsHookEx(hook)
        del hook_ref

    snap_end = snapshot_system()
    rows = derive(raw)
    meta = {
        "duration_s": (perf1 - perf0) / 1e9,
        "requested_s": requested_s if requested_s else "Ctrl+C",
        "perf0": perf0, "wall0": wall0,
        "host": host, "csv_path": csv_path, "rep_path": rep_path,
        "snapshot_start": snap_start, "snapshot_end": snap_end,
        "interrupted": interrupted,
    }
    a = analyze(rows, sched_over, meta)
    report = render_report(a, meta)

    write_text_atomic(csv_path, csv_text(rows), finalize=True)   # 原子落盘
    write_text_atomic(rep_path, report, finalize=True)

    print()
    print(report)
    print(f"\n[完成] CSV: {csv_path}")
    print(f"[完成] 报告: {rep_path}")
    return 0


# ============================================================
# 自测（合成事件序列，不碰真实键盘、不写项目文件）
# ============================================================

def run_selftest():
    """断言统计口径与异常计数全部正确；PASS → exit 0，FAIL → exit 1。"""
    failures = []
    checks = [0]

    def check(cond, msg):
        checks[0] += 1
        if not cond:
            failures.append(msg)

    MS = 1_000_000

    def ev(t_ms, vk, wkind, proc_ms):
        return (int(t_ms * MS), vk, wkind, int(t_ms * MS / 1000), float(proc_ms))

    # 合成剧本（时间单位 ms）：
    #  正常打字 4 键 → 突发 3 连按 → 一次 700ms 故意长按（带 3 个自动重复）
    #  → 一次 450ms 粘滞样按键（无自动重复）→ 950ms 人为停顿 → 收尾
    #  处理延迟尖峰 250ms 打在事件 21（粘滞键的 up）上。
    raw = [
        ev(100, 65, "down", 2), ev(140, 65, "up", 1),
        ev(250, 66, "down", 3), ev(300, 66, "up", 2),
        ev(420, 67, "down", 1), ev(470, 67, "up", 2),
        ev(560, 68, "down", 2), ev(610, 68, "up", 3),
        ev(620, 69, "down", 2), ev(630, 70, "down", 4), ev(640, 71, "down", 1),
        ev(680, 69, "up", 2), ev(690, 70, "up", 1), ev(700, 71, "up", 2),
        ev(1100, 72, "down", 2),
        ev(1650, 72, "down", 2), ev(1683, 72, "down", 1), ev(1716, 72, "down", 2),
        ev(1800, 72, "up", 2),
        ev(2000, 73, "down", 3), ev(2450, 73, "up", 250),
        ev(3400, 74, "down", 2), ev(3450, 74, "up", 2),
    ]
    sched = [0.5, 0.3, 5.0, 120.0]
    meta = {"perf0": 0, "wall0": time.time(), "duration_s": 3.45,
            "requested_s": "selftest", "host": "SELFTEST", "csv_path": "-",
            "rep_path": "-", "snapshot_start": None, "snapshot_end": None}

    rows = derive(raw)
    a = analyze(rows, sched, meta)

    # —— 结构断言 ——
    check(a["n_rows"] == 23, f"事件总数应为 23，实际 {a['n_rows']}")
    check((a["n_down"], a["n_repeat"], a["n_up"]) == (10, 3, 10),
          f"down/repeat/up 应为 10/3/10，实际 {a['n_down']}/{a['n_repeat']}/{a['n_up']}")
    check(a["unique_vk"] == 10, f"独立按键数应为 10，实际 {a['unique_vk']}")
    check(rows[15][3] == "repeat" and abs(rows[15][5] - 550.0) < 1e-9,
          "同键未抬起的重复 down 应被归类 repeat 且保留原始间隔")

    # —— 按住时长 ——
    holds = [r[4] for r in rows if r[4] is not None]
    check(len(holds) == 10, f"按住样本数应为 10，实际 {len(holds)}")
    check(abs(a["hold"]["p50"] - 55.0) < 1e-6,
          f"hold p50 应为 55.0，实际 {a['hold']['p50']}")
    check(abs(a["hold"]["p95"] - 587.5) < 1e-6,
          f"hold p95 应为 587.5，实际 {a['hold']['p95']}")
    check(abs(a["hold"]["p99"] - 677.5) < 1e-6,
          f"hold p99 应为 677.5，实际 {a['hold']['p99']}")
    check(abs(a["hold"]["max"] - 700.0) < 1e-6,
          f"hold max 应为 700.0，实际 {a['hold']['max']}")

    # —— 相邻间隔（剔除 down→repeat 的 550ms 系统重复延迟）——
    check(a["gap"]["n"] == 21, f"有效间隔数应为 21（23-1-1），实际 {a['gap']['n']}")
    check(abs(a["gap"]["p50"] - 50.0) < 1e-6, f"gap p50 应为 50.0，实际 {a['gap']['p50']}")
    check(abs(a["gap"]["p95"] - 450.0) < 1e-6, f"gap p95 应为 450.0，实际 {a['gap']['p95']}")
    check(abs(a["gap"]["p99"] - 850.0) < 1e-6, f"gap p99 应为 850.0，实际 {a['gap']['p99']}")
    check(abs(a["gap"]["max"] - 950.0) < 1e-6, f"gap max 应为 950.0，实际 {a['gap']['max']}")

    # —— 卡顿点 ——
    stall_gaps = [s["gap_ms"] for s in a["stalls"]]
    check(stall_gaps == [400.0, 450.0, 950.0],
          f"卡顿点间隔应为 [400,450,950]（550=系统重复延迟、200=边界不计），实际 {stall_gaps}")

    # —— 处理延迟 ——
    check(a["proc"]["n"] == 23, f"处理延迟样本数应为 23，实际 {a['proc']['n']}")
    check(abs(a["proc"]["p50"] - 2.0) < 1e-6, f"proc p50 应为 2.0，实际 {a['proc']['p50']}")
    check(abs(a["proc"]["p95"] - 3.9) < 1e-6, f"proc p95 应为 3.9，实际 {a['proc']['p95']}")
    check(abs(a["proc"]["max"] - 250.0) < 1e-6, f"proc max 应为 250.0，实际 {a['proc']['max']}")
    check(a["proc_over_n"] == 1, f"proc>200ms 次数应为 1，实际 {a['proc_over_n']}")

    # —— 粘滞判定 ——
    check(a["hold_over_n"] == 2, f"按住>300ms 总数应为 2（长按+粘滞），实际 {a['hold_over_n']}")
    check(len(a["sticky_susp"]) == 1,
          f"疑似粘滞（>300ms 且无 repeat 伴随）应为 1，实际 {len(a['sticky_susp'])}")
    if a["sticky_susp"]:
        check(abs(a["sticky_susp"][0]["hold_ms"] - 450.0) < 1e-6,
              f"疑似粘滞样本应为 450ms 那次，实际 {a['sticky_susp'][0]['hold_ms']}")

    # —— 调度延迟 ——
    check(abs(a["sched"]["p50"] - 2.75) < 1e-6, f"sched p50 应为 2.75，实际 {a['sched']['p50']}")
    check(abs(a["sched"]["p95"] - 102.75) < 1e-6, f"sched p95 应为 102.75，实际 {a['sched']['p95']}")
    check(abs(a["sched"]["max"] - 120.0) < 1e-6, f"sched max 应为 120.0，实际 {a['sched']['max']}")
    check(a["sched_over_n"] == 1, f"sched>100ms 次数应为 1，实际 {a['sched_over_n']}")

    # —— 结论与报告渲染 ——
    joined = " | ".join(a["verdicts"])
    check("迟滞" in joined and "粘滞" in joined, f"结论应同时含迟滞与粘滞，实际：{joined}")
    rep = render_report(a, meta)
    check("隐私声明" in rep and "vk=" in rep, "报告应含隐私声明与卡顿点明细")
    check("无数据" not in rep.split("[结论]")[0], "报告统计段不应出现「无数据」")
    for banned in ("GetKeyNameText", "ToUnicode", "GetWindowText"):
        check(banned not in rep, f"报告不得出现敏感 API 名 {banned}")

    # —— CSV 文本与原子写 ——
    text = csv_text(rows)
    parsed = list(csv.reader(io.StringIO(text)))
    check(parsed[0] == CSV_HEADER, f"CSV 表头应为 {CSV_HEADER}，实际 {parsed[0]}")
    check(len(parsed) == 24, f"CSV 应为 1 表头 + 23 行，实际 {len(parsed)} 行")
    check(all(row[2].isdigit() for row in parsed[1:]), "CSV 的 vk 列必须全是数字（隐私）")
    check(all(row[3] in KIND_TOKENS for row in parsed[1:]), "CSV kind 列只能是 down/repeat/up")
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "a.txt")
        write_text_atomic(p1, "内容", finalize=True)
        check(open(p1, encoding="utf-8").read() == "内容", "原子写应落盘成功且内容一致")
        write_text_atomic(p1, "草稿", finalize=False)
        check(open(p1, encoding="utf-8").read() == "内容" and os.path.exists(p1 + ".tmp"),
              "finalize=False 只写 tmp，不覆盖目标")

    print("── 自测合成数据报告（供 报告_模板.md 引用）──")
    print(rep)
    if failures:
        print("\nSELFTEST: FAIL（%d 项断言未过）" % len(failures))
        for f_ in failures:
            print(f"  ✗ {f_}")
        return 1
    print(f"SELFTEST: PASS（{checks[0]} 项断言全部通过）")
    return 0


# ============================================================
# 离线分析（2026-11-20 增加：给常驻采样用，事后对 CSV 出报告）
# ============================================================

def read_csv_rows(path):
    """把 csv_text() 写出的 CSV 读回 rows 结构；空文件返回 []。"""
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for line in reader:
            if len(line) < 7 or not line[0]:
                continue
            def num(x):
                return None if x == "" else float(x)
            rows.append([int(line[0]), int(line[1]), int(line[2]), line[3],
                         num(line[4]), num(line[5]), num(line[6])])
    return rows


def run_analyze(path):
    """对已落盘的 CSV 出报告（--analyze），不重新挂钩子。"""
    if not os.path.exists(path):
        print(f"错误：找不到 {path}", file=sys.stderr)
        return 2
    rows = read_csv_rows(path)
    dur = (rows[-1][0] - rows[0][0]) / 1e9 if len(rows) >= 2 else 0.0
    meta = {
        "duration_s": dur,
        "requested_s": "离线分析",
        "host": f"{platform.node()} / Windows {platform.version()} / Python {sys.version.split()[0]}",
        "csv_path": path,
        "rep_path": "",
        "snapshot_start": None,
        "snapshot_end": None,
    }
    a = analyze(rows, [], meta)
    rep = render_report(a, meta)
    rep_path = os.path.splitext(path)[0] + "_report.txt"
    write_text_atomic(rep_path, rep)
    print(rep)
    print(f"[完成] 报告: {rep_path}")
    return 0


# ============================================================
# 入口
# ============================================================

def main(argv=None):
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        prog="kbdprobe",
        description="键盘延迟探针：只记 VK 数字/时间戳/事件类型，绝不记录按键内容。")
    ap.add_argument("--seconds", type=float, default=None,
                    help="采样秒数；缺省 = 采到 Ctrl+C")
    ap.add_argument("--out", default=None,
                    help="输出目录（默认 <脚本目录>\\samples）")
    ap.add_argument("--selftest", action="store_true",
                    help="合成数据自测（不碰真实键盘、不写文件）")
    ap.add_argument("--analyze", default=None, metavar="CSV",
                    help="离线分析已落盘的 CSV，输出 <同名>_report.txt（不挂钩子）")
    args = ap.parse_args(argv)

    if args.analyze:
        return run_analyze(args.analyze)

    if args.selftest:
        if args.seconds is not None:
            ap.error("--selftest 不接受 --seconds")
        return run_selftest()
    if args.seconds is not None and args.seconds <= 0:
        ap.error("--seconds 必须 > 0")
    return run_sampling(args)


if __name__ == "__main__":
    sys.exit(main())

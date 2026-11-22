# -*- coding: utf-8 -*-
"""Razer 无线鼠标电量读取（HID 厂商协议，不依赖 Synapse）。

协议出处：OpenRazer / xzeldon/razer-battery-report（已在本机 Viper V2 Pro 实测验证）：
  * 90 字节帧：[0]=status [1]=tid [2:4]=remaining [4]=protocol(0x02) [5]=data_size
    [6]=class(0x07) [7]=command_id [8:88]=args [88]=xor-crc(bytes[2:88]) [89]=0
  * 电量查询 class=0x07 id=0x80，充电查询 id=0x84；答案在 arguments[1]
    **刻度（2026-11-22 实测修正）**：该字节是**直接百分比 0-100**，255 是「充满」哨兵——
    本机完整充放周期实测：充电爬升 3→12→67（%）、充满读 255、满电使用一天后 31。
    按 /255 刻度换算会在满格附近严重失真，**勿改回 /255**。
  * hidapi 线上格式 = 报告 ID(0x00) + 90 字节帧 = 91 字节，特征报告缓冲必须严格 91
  * 事务 ID：新一代机型 0x1F（Viper V2 Pro 实测），兼容候选 0x3F / 0xFF
  * 有线/dongle 是两个端点（PID 不同），插线时 dongle 空响应——按枚举结果逐端点尝试
  * args 全零回显 = 鼠标睡眠或空端点 → 视为无数据（不显示 0%）
"""
import time

import hid

VENDOR_ID = 0x1532
REPORT_LEN = 90
TID_CANDIDATES = (0x1F, 0x3F, 0xFF)
CMD_BATTERY = 0x80
CMD_CHARGING = 0x84

_tid_cache = None  # 命中的事务 ID（跨查询复用，避免每次盲试）


def _frame(tid, cid):
    b = bytearray(REPORT_LEN)
    b[1] = tid
    b[5] = 0x02
    b[6] = 0x07
    b[7] = cid
    crc = 0
    for x in b[2:88]:
        crc ^= x
    b[88] = crc
    return bytes(b)


def _mouse_collections():
    """鼠标控制集合：usage_page=0x0001 & usage=0x0002，有线端点优先。"""
    found = [d for d in hid.enumerate(VENDOR_ID)
             if d['usage_page'] == 0x0001 and d['usage'] == 0x0002]
    found.sort(key=lambda d: d['interface_number'] != 0)
    return found


def _query_once(dev, tid, cid):
    wire = bytes([0]) + _frame(tid, cid)
    dev.send_feature_report(wire)
    time.sleep(0.031)
    resp = dev.get_feature_report(0, REPORT_LEN + 1)
    if len(resp) != REPORT_LEN + 1:
        return None
    f = resp[1:REPORT_LEN + 1]
    if f[6] == 0x07 and f[7] == cid and (f[8] or f[9]):
        return f  # 有效应答（echo 正确且 arguments 非全零）
    return None


def _query_with_tid(dev, cid):
    global _tid_cache
    tids = (_tid_cache,) if _tid_cache else ()
    for tid in tids + tuple(t for t in TID_CANDIDATES if t != _tid_cache):
        args = None
        for attempt in range(3):
            try:
                args = _query_once(dev, tid, cid)
            except Exception:
                args = None
                time.sleep(0.01)
            if args:
                _tid_cache = tid
                return args
    return None


def read_battery():
    """返回 (percent:int, charging:bool|None)；读不到（无设备/睡眠/Synapse 独占）返回 None。"""
    for d in _mouse_collections():
        dev = hid.device()
        try:
            dev.open_path(d['path'])
        except Exception:
            continue
        try:
            args = _query_with_tid(dev, CMD_BATTERY)
            if not args:
                continue
            raw = args[1]
            if raw == 0:
                continue  # 空回显/睡眠（255=充满哨兵是合法值）
            percent = min(raw, 100)  # 直接百分比；255 哨兵 → 100%
            charging = None
            chg = _query_with_tid(dev, CMD_CHARGING)
            if chg:
                charging = chg[1] != 0
            return percent, charging
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return None


def read_line():
    """面板显示文本：'鼠标 73%' / '鼠标 73%（充电中）'；无数据返回 None。"""
    try:
        result = read_battery()
    except Exception:
        return None
    if not result:
        return None
    percent, charging = result
    if charging:
        return '鼠标 %d%%（充电中）' % percent
    return '鼠标 %d%%' % percent

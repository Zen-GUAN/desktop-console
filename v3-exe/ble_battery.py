# -*- coding: utf-8 -*-
"""AULA（狼蛛）键盘蓝牙电量读取——BLE 标准电池服务（GATT 0x180F/0x2A19）。

实测结论（AULA-S98，本机 2026-11-21）：
  * 蓝牙 5.2（BLE）模式：标准电池服务可读 ✓
  * 蓝牙 3.0（经典 HID）模式：无电池服务 ✗
  * 2.4G 接收器 / 有线模式：固件不上报 ✗
MAC 获取：注册表 Enum\\BTHLEDEVICE 实例 ID 尾段（DEVPKEY_Bluetooth_DeviceAddress 同源），
无需 admin。依赖 winsdk（PyInstaller --collect-all winsdk）。后台线程调用（见 app.py）。
"""
import asyncio
import uuid
import winreg

BATTERY_SERVICE = uuid.UUID('0000180f-0000-1000-8000-00805f9b34fb')
BATTERY_LEVEL = uuid.UUID('00002a19-0000-1000-8000-00805f9b34fb')
KEYBOARD_NAMES = ('S98', 'AULA')
ENUM_ROOT = r'SYSTEM\CurrentControlSet\Enum\BTHLEDEVICE'


def _ble_macs():
    """从注册表枚举 BLE 蓝牙设备 MAC（实例 ID 尾段 16 位 hex）。"""
    macs = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ENUM_ROOT) as root:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                seg = subkey_name.rsplit('_', 1)[-1]
                if len(seg) == 12 and all(c in '0123456789ABCDEFabcdef' for c in seg):
                    macs.append(int(seg, 16))
    except OSError:
        pass
    return macs


async def _read_async():
    from winsdk.windows.devices.bluetooth import BluetoothLEDevice

    macs = _ble_macs()
    if not macs:
        return None
    named = []
    for mac in macs:
        try:
            ble = await BluetoothLEDevice.from_bluetooth_address_async(mac)
        except Exception:
            ble = None
        if ble is None:
            continue
        try:
            if _match(ble.name):
                named.append(ble)
            else:
                named.append(ble)  # 全部尝试，电池服务命中才算数
        except Exception:
            named.append(ble)

    for ble in named:
        try:
            result = await ble.get_gatt_services_for_uuid_async(BATTERY_SERVICE)
            for svc in result.services:
                ch_result = await svc.get_characteristics_for_uuid_async(BATTERY_LEVEL)
                for ch in ch_result.characteristics:
                    read = await ch.read_value_async()
                    data = bytes(read.value)
                    if data:
                        return (ble.name or '键盘', data[0])
        except Exception:
            continue
        finally:
            try:
                ble.close()
            except Exception:
                pass
    return None


def _match(name):
    n = (name or '').upper()
    return any(k in n for k in KEYBOARD_NAMES)


def read_battery():
    """返回 (设备名, percent)；无蓝牙键盘/读取失败返回 None。"""
    try:
        return asyncio.run(_read_async())
    except Exception:
        return None


def read_line():
    """面板显示文本：'键盘 100%'；无数据返回 None。"""
    result = read_battery()
    if not result:
        return None
    return '键盘 %d%%' % result[1]

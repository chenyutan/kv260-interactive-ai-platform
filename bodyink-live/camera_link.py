"""Read-only USB link diagnosis for the project's identified UVC camera."""
import math
from pathlib import Path


_VENDOR = '0bda'
_PRODUCT = '5858'
_SERIAL = '__BODYINK_CAMERA_SERIAL__'


def _read(device, name):
    return (device / name).read_text(encoding='utf-8').strip()


def read_camera_link(sys_root=Path('/sys')):
    """Report identity and negotiated USB speed; never change capture or hardware.

    `ready_for_1080p60_yuyv` checks the USB link-rate prerequisite only. Actual
    capture and inference health remain separate runtime measurements.
    """
    result = {'present':False, 'speed_mbps':None, 'product':None,
              'ready_for_1080p60_yuyv':False, 'message':''}
    devices = Path(sys_root) / 'bus/usb/devices'
    try:
        entries = sorted(devices.iterdir(), key=lambda entry:entry.name)
    except OSError:
        result['message'] = '无法读取 USB 设备列表，暂不能确认摄像头连接状态。'
        return result

    matches, incomplete = [], []
    for device in entries:
        try:
            vendor = _read(device, 'idVendor').lower()
        except FileNotFoundError:
            # USB interface entries normally have no device identity attributes.
            continue
        except (OSError, UnicodeError):
            incomplete.append(device.name)
            continue
        if vendor != _VENDOR:
            continue
        try:
            if _read(device, 'idProduct').lower() != _PRODUCT:
                continue
            serial = _read(device, 'serial')
        except (OSError, UnicodeError):
            incomplete.append(device.name)
            continue
        if serial == _SERIAL:
            matches.append(device)

    if not matches:
        result['message'] = ('USB 设备身份信息读取失败，暂不能确认指定摄像头；请检查连接状态。'
                             if incomplete else
                             '未连接指定摄像头（0bda:5858，序列号 __BODYINK_CAMERA_SERIAL__）。')
        return result
    result['present'] = True
    if len(matches) != 1:
        result['message'] = ('检测到多个相同身份的摄像头（%d 个），无法唯一确定 USB 链路；'
                             '1080P60 YUYV 暂不可用。' % len(matches))
        return result
    if incomplete:
        result['message'] = '已发现摄像头，但部分 USB 身份信息读取失败，无法确认设备唯一性。'
        return result

    device = matches[0]
    fields, failed = {}, []
    for name in ('speed', 'product', 'devpath'):
        try:
            fields[name] = _read(device, name)
            if not fields[name]:
                failed.append(name)
        except (OSError, UnicodeError):
            failed.append(name)
    result['product'] = fields.get('product') or None
    try:
        speed = float(fields.get('speed', ''))
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('invalid speed')
        result['speed_mbps'] = speed
    except ValueError:
        if 'speed' not in failed:
            failed.append('speed')

    # A hotplug can replace the directory between identity and attribute reads.
    # Do not label its replacement or a disconnected device as a healthy camera.
    try:
        identity = (_read(device, 'idVendor').lower(), _read(device, 'idProduct').lower(),
                    _read(device, 'serial'))
    except (OSError, UnicodeError):
        identity = None
    if identity != (_VENDOR, _PRODUCT, _SERIAL):
        result.update(present=False, speed_mbps=None, product=None,
                      message='读取期间摄像头连接或身份发生变化，等待下一次检查。')
        return result
    if failed:
        result['message'] = '已发现摄像头，但 USB 属性读取失败（%s），暂不能确认 1080P60 YUYV 链路。' % '、'.join(failed)
        return result

    speed = result['speed_mbps']
    label = '%s（USB %s，端口 %s）' % (result['product'], device.name, fields['devpath'])
    if speed >= 5000:
        result['ready_for_1080p60_yuyv'] = True
        result['message'] = '%s：USB 链路 %g Mbps，达到 1080P60 YUYV 的链路速率要求。' % (label, speed)
    else:
        result['message'] = ('%s：USB 链路仅 %g Mbps，未达到 1080P60 YUYV 所需的 USB 3.0 '
                             '链路速率（至少 5000 Mbps）。' % (label, speed))
    return result

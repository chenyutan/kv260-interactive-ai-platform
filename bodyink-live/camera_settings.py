"""Bounded, verified exposure controls for the installed USB Camera3.

All public methods return dictionaries, including failures. ``applied`` means
the entire requested control set was read back exactly; ``saved`` additionally
means the atomic settings-file replacement succeeded. No capture/FPS claim is
made here. The capture service owns the stream and must start before restore.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading


DEVICE = '/dev/v4l/by-id/usb-Generic_USB_Camera3___BODYINK_CAMERA_SERIAL__-video-index0'
USB2_CONTROL_DEVICE = '/dev/v4l/by-id/usb-Generic_USB_Camera2___BODYINK_CAMERA_SERIAL__-video-index0'
# The measured default of this exact camera. Hardware AE does not automatically
# undo a previously selected high manual gain, so restore this known default.
AUTO_GAIN = 64
CONTROLS = ('auto_exposure', 'exposure_time_absolute', 'gain',
            'exposure_dynamic_framerate')
COMMAND_TIMEOUT = 2.0
LOCK_TIMEOUT = 2.0


class _CameraError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class CameraSettings:
    def __init__(self, root):
        self.path = Path(root) / 'camera-settings.json'
        self._lock = threading.Lock()

    @staticmethod
    def _result(operation):
        return dict(ok=False, operation=operation, actual=None, requested=None,
                    applied=False, saved=False, skipped=False,
                    hardware_may_have_changed=False, error=None, message='')

    @staticmethod
    def _validate(exposure_100us, gain, mode='manual'):
        if mode not in ('manual', 'auto'):
            raise _CameraError('invalid_mode', '相机曝光模式必须为 manual 或 auto。')
        if mode == 'auto':
            return
        if type(exposure_100us) is not int or not 50 <= exposure_100us <= 160:
            raise _CameraError('invalid_exposure', '曝光必须为 50–160 的整数（单位 100 微秒）。')
        if type(gain) is not int or not 0 <= gain <= 128:
            raise _CameraError('invalid_gain', '增益必须为 0–128 的整数。')

    def _run(self, option):
        # Control-only fallback for the same serial number when enumerated at
        # USB2 speed. The capture owner's USB3/1080p60 requirement is unchanged.
        device = DEVICE
        if not Path(DEVICE).exists() and Path(USB2_CONTROL_DEVICE).exists():
            device = USB2_CONTROL_DEVICE
        command = ['/usr/bin/v4l2-ctl', '--device', device, option]
        try:
            proc = subprocess.run(command, shell=False, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding='utf-8', errors='replace',
                                  timeout=COMMAND_TIMEOUT, check=False,
                                  env=dict(os.environ, LC_ALL='C'))
        except subprocess.TimeoutExpired:
            raise _CameraError('camera_timeout', '相机控制命令超时，未确认设置成功。') from None
        except OSError as exc:
            raise _CameraError('camera_unavailable', '无法调用相机控制工具：' + str(exc)[:240]) from None
        if proc.returncode:
            detail = (proc.stderr.strip() or proc.stdout.strip())[:240]
            raise _CameraError('camera_command_failed', '相机控制失败：' + detail)
        return proc.stdout

    def _read_actual(self):
        output = self._run('--get-ctrl=' + ','.join(CONTROLS))
        values = {}
        for line in output.splitlines():
            match = re.fullmatch(r'\s*([a-z_]+):\s*(-?\d+)(?:\s+\([^\n]*\))?\s*', line)
            if match and match[1] in CONTROLS:
                if match[1] in values:
                    raise _CameraError('invalid_readback', '相机返回了重复的控制值，无法确认设置。')
                values[match[1]] = int(match[2])
        if set(values) != set(CONTROLS):
            raise _CameraError('invalid_readback', '相机未完整返回曝光、增益和帧率控制值。')
        mode = {1: 'manual', 3: 'auto'}.get(values['auto_exposure'], 'unknown')
        note = ('自动模式下此曝光值是相机控制项读回，可能不是逐帧实际曝光。'
                if mode == 'auto' else '相机曝光控制项读回值。')
        return dict(mode=mode, exposure_100us=values['exposure_time_absolute'],
                    exposure_ms=values['exposure_time_absolute'] / 10.0,
                    exposure_readback_note=note,
                    gain=values['gain'], auto_exposure=values['auto_exposure'],
                    exposure_dynamic_framerate=values['exposure_dynamic_framerate'])

    def _save(self, requested):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.path.parent, prefix='.camera-settings-', suffix='.tmp',
                    delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(dict(schema_version=2, **requested), stream, ensure_ascii=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    # A failed cleanup must not conceal the original save error.
                    pass

    def _invoke(self, operation, callback):
        result = self._result(operation)
        if not self._lock.acquire(timeout=LOCK_TIMEOUT):
            result.update(error='camera_busy', message='正在调整相机，请稍后重试。')
            return result
        try:
            callback(result)
        except _CameraError as exc:
            result.update(error=exc.code, message=str(exc))
        finally:
            self._lock.release()
        return result

    def read(self):
        """Read just four hardware controls; does not write or load saved values."""
        def read_into(result):
            result.update(actual=self._read_actual(), ok=True, message='已读取相机当前设置。')
        return self._invoke('read', read_into)

    def _apply(self, result, exposure_100us, gain, mode='manual'):
        self._validate(exposure_100us, gain, mode)
        requested = dict(mode=mode)
        if mode == 'manual':
            requested.update(exposure_100us=exposure_100us, gain=gain)
        else:
            requested['gain'] = AUTO_GAIN
        result['requested'] = requested
        result['hardware_may_have_changed'] = True
        try:
            if mode == 'manual':
                # Switch to manual first: exposure may be inactive in auto mode.
                self._run('--set-ctrl=auto_exposure=1,exposure_dynamic_framerate=0')
                self._run('--set-ctrl=exposure_time_absolute=%d,gain=%d' % (exposure_100us, gain))
            else:
                # AE owns exposure only. Restore the known default gain so a
                # previous manual boost cannot leave automatic exposure clipped.
                self._run('--set-ctrl=auto_exposure=3,exposure_dynamic_framerate=0,gain=%d' % AUTO_GAIN)
            actual = self._read_actual()
            result['actual'] = actual
        except _CameraError:
            # Failed ioctls may already have applied a subset. Report real values
            # when readable, without claiming the operation succeeded.
            try:
                result['actual'] = self._read_actual()
            except _CameraError:
                pass
            raise
        expected = dict(requested, auto_exposure=1 if mode == 'manual' else 3,
                        exposure_dynamic_framerate=0)
        if any(actual[key] != value for key, value in expected.items()):
            raise _CameraError('readback_mismatch', '相机实际读回值与请求不一致，未保存；请查看当前值后重试。')
        result['applied'] = True
        try:
            self._save(requested)
        except OSError as exc:
            raise _CameraError('save_failed', '相机已调整，但保存失败，重启后不能保证恢复：' + str(exc)[:240]) from None
        message = ('硬件自动曝光已启用，增益恢复默认 64，禁用动态降帧；已读回确认并保存。'
                   if mode == 'auto' else '相机设置已应用、读回确认并保存。')
        result.update(ok=True, saved=True, message=message)

    def apply(self, exposure_100us=None, gain=None, mode='manual'):
        """Manual: apply 50..160/0..128 integers; auto: ignore these arguments.

        ``apply(mode='auto')`` enables hardware AE without setting exposure and
        restores this model's measured default gain 64 (not automatic gain).
        Both modes disable dynamic frame rate and verify controls.
        """
        return self._invoke('apply', lambda result: self._apply(result, exposure_100us, gain, mode))

    def restore_saved(self):
        """Restore after capture startup; absent settings leave hardware alone."""
        def restore(result):
            try:
                with self.path.open('r', encoding='utf-8') as stream:
                    content = stream.read(4097)
            except FileNotFoundError:
                result.update(ok=True, skipped=True, message='没有已保存设置，保持相机当前设置。')
                return
            except UnicodeError:
                raise _CameraError('invalid_saved_settings', '已保存设置不是有效 UTF-8 文件，未调整相机。') from None
            except OSError as exc:
                raise _CameraError('saved_settings_unreadable', '无法读取已保存设置：' + str(exc)[:240]) from None
            try:
                if len(content) > 4096:
                    raise ValueError('settings file too large')
                saved = json.loads(content)
                if (not isinstance(saved, dict) or type(saved.get('schema_version')) is not int
                        or saved['schema_version'] not in (1, 2)):
                    raise ValueError('unsupported settings schema')
                # Version 1 without mode was always a manual-only profile.
                mode = saved.get('mode', 'manual' if saved['schema_version'] == 1 else None)
                self._validate(saved.get('exposure_100us'), saved.get('gain'), mode)
            except (ValueError, _CameraError) as exc:
                raise _CameraError('invalid_saved_settings', '已保存设置无效，未调整相机：' + str(exc)[:240]) from None
            self._apply(result, saved.get('exposure_100us'), saved.get('gain'), mode)
        return self._invoke('restore_saved', restore)

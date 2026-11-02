"""Read-only local device inventory. Never opens an audio stream or changes defaults."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess


COMMANDS = (
    ('pulse_info', ('pactl', 'info')),
    ('pulse_sinks', ('pactl', 'list', 'short', 'sinks')),
    ('pulse_sink_details', ('pactl', 'list', 'sinks')),
    ('alsa_cards', ('aplay', '-l')),
    ('alsa_names', ('aplay', '-L')),
    ('usb_devices', ('lsusb',)),
    ('gstreamer_version', ('gst-inspect-1.0', '--version')),
    ('gstreamer_audio_sinks', ('gst-device-monitor-1.0', 'Audio/Sink')),
)


def _text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value or ''


def collect(timeout=3.0):
    report = {
        'schema': 1,
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'platform': platform.platform(),
        'read_only': True,
        'audio_played': False,
        'commands': {},
        'note': 'Enumeration is not proof of audible output, controller compatibility, or wireless latency.',
    }
    for name, args in COMMANDS:
        executable = shutil.which(args[0])
        entry = {'argv': list(args)}
        if executable is None:
            entry.update(status='unavailable', error='Command not installed')
        else:
            try:
                result = subprocess.run([executable, *args[1:]], capture_output=True,
                                        text=True, encoding='utf-8', errors='replace', timeout=timeout,
                                        check=False)
                entry.update(status='ok' if result.returncode == 0 else 'failed',
                             exit_code=result.returncode, stdout=result.stdout[:65536], stderr=result.stderr[:16384])
            except subprocess.TimeoutExpired as exc:
                # Device monitor intentionally keeps watching; its bounded initial
                # inventory is useful even though this read-only process is stopped.
                entry.update(status='timed_out', stdout=_text(exc.stdout)[:65536], stderr=_text(exc.stderr)[:16384])
            except OSError as exc:
                entry.update(status='failed', error=str(exc))
        report['commands'][name] = entry
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON report file; otherwise print only')
    args = parser.parse_args(argv)
    data = json.dumps(collect(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data + '\n', encoding='utf-8')
    print(data)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

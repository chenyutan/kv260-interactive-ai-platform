"""Read-only 30 s input liveness probe; records counts, NEVER key codes/values.

Run with sudo on KV260 only during an agreed input test. No event grab, no writes
to device settings, no camera reads. Output is JSONL to stdout.
"""
import argparse
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def snapshot():
    interrupts = read('/proc/interrupts') or ''
    return {
        'uptime': read('/proc/uptime'),
        'pressure': {n: read('/proc/pressure/' + n) for n in ('cpu', 'io', 'memory')},
        'interrupts': [x for x in interrupts.splitlines() if 'xhci' in x or 'fd4a' in x],
        'usb_present': Path('/sys/bus/usb/devices/1-1.2').exists(),
        'usb_runtime': read('/sys/bus/usb/devices/1-1.2/power/runtime_status'),
    }


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return {'returncode': result.returncode, 'stdout': result.stdout[-20000:],
                'stderr': result.stderr[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'error': type(exc).__name__}


def evidence():
    return {
        'processes': command(['ps', '-eo', 'pid,ppid,stat,pcpu,pmem,ni,rtprio,comm', '--sort=-pcpu']),
        'kernel': command(['journalctl', '-b', '-k', '-n', '60', '-o', 'short-monotonic', '--no-pager']),
        'desktop': command(['journalctl', '-b', '-n', '45', '-o', 'short-monotonic', '--no-pager',
                            '_COMM=gnome-shell', '_COMM=gdm3', '_COMM=Xorg']),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--count-input-events', action='store_true',
                        help='Optional event counts only; default does not open any input device')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error('seconds must be 1..120')
    # Native struct input_event: timeval + type/code/value. Do not unpack fields.
    size = struct.calcsize('@llHHi')
    fds = {}
    for event in (Path('/sys/class/input').glob('event*') if args.count_input_events else []):
        name = read(event / 'device/name') or ''
        if 'EWEADN X23SE' in name:
            path = '/dev/input/' + event.name
            try:
                fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = (path, name)
            except OSError as exc:
                print(json.dumps({'open_error': path, 'errno': exc.errno}), flush=True)
    print(json.dumps({'type': 'start', 'devices': list(fds.values()), 'snapshot': snapshot(),
                      'evidence': evidence()}), flush=True)
    counts = {path: 0 for path, _ in fds.values()}
    remainder = {fd: 0 for fd in fds}
    start = tick = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            ready, _, _ = select.select(list(fds), [], [], 0.1)
            for fd in ready:
                try:
                    data = os.read(fd, size * 64)
                    if not data:
                        raise OSError(19, 'device ended')
                    total = remainder[fd] + len(data)
                    counts[fds[fd][0]] += total // size
                    remainder[fd] = total % size
                    del data
                except OSError as exc:
                    print(json.dumps({'device_error': fds[fd][0], 'errno': exc.errno}), flush=True)
                    os.close(fd)
                    del fds[fd]
            if time.monotonic() - tick >= 1:
                print(json.dumps({'type': 'sample', 'elapsed': time.monotonic() - start,
                                  'event_counts': counts, 'snapshot': snapshot()}), flush=True)
                counts = {path: 0 for path in counts}
                tick = time.monotonic()
    finally:
        for fd in fds:
            os.close(fd)
    print(json.dumps({'type': 'end', 'snapshot': snapshot(), 'evidence': evidence()}), flush=True)


if __name__ == '__main__':
    main()

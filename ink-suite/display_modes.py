"""Explicit low-resolution XRandR trials with an independent 15-second rollback.

No display mutation occurs on import or ``inspect``. ``try`` writes the complete
baseline first, then arms a detached watchdog before touching XRandR. Only an
explicit matching confirmation token persists a preference. A successful
XRandR command is never treated as proof that the monitor is visibly working.
Complex multi-output/transformed desktops are rejected rather than restored
incompletely. This utility does not modify Xorg, EDID, kernel or camera settings.
"""
import argparse
try:
    import fcntl
except ImportError:  # Pure fake-XRandR tests may run on the Windows host.
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

ROLLBACK_SECONDS = 15.
SUPPORTED_SIZES = ('960x540', '800x450', '1024x576')
DEFAULT_STATE = Path.home() / '.local/state/ink-suite/display-mode'
SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:+-]*\Z')


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=5.,
                            stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(' '.join(argv[:3]) + ': ' + (result.stderr or result.stdout)[-1800:])
    return result.stdout


def parse_query(text):
    outputs = [];output = None
    for line in text.splitlines():
        match = re.match(r'^([\w.:-]+) (connected|disconnected)(.*)$', line)
        if match:
            name, link, tail = match.groups()
            geometry = re.search(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', tail)
            output = dict(name=name, connected=link == 'connected',
                          primary=bool(re.search(r'\bprimary\b', tail)), modes=[])
            if geometry:
                width, height, x, y = map(int, geometry.groups())
                output.update(size=[width, height], position=[x, y])
                rotation = tail[geometry.end():].split('(')[0].strip()
                output['rotation'] = rotation or 'normal'
            outputs.append(output);continue
        if output and re.match(r'^\s+\S+\s+[\d.]', line):
            parts = line.split();mode = parts[0]
            rates = parts[1:]
            output['modes'].append(mode)
            for rate in rates:
                if '*' in rate:
                    output['current_mode'] = mode
                    output['current_rate'] = rate.rstrip('*+')
    return outputs


def inspect(runner=run):
    query = runner(['xrandr', '--query'])
    verbose = runner(['xrandr', '--verbose'])
    outputs = parse_query(query)
    for output in outputs:
        name = output['name']
        match = re.search(r'^' + re.escape(name) + r' connected\b(.*?)(?=^\S|\Z)',
                          verbose, re.M | re.S)
        block = match.group(0) if match else ''
        edid = re.search(r'^\s+EDID:\s*\n((?:\s+[a-fA-F0-9]{32}\s*\n)+)', block, re.M)
        output['edid_sha256'] = (hashlib.sha256(bytes.fromhex(''.join(edid.group(1).split()))).hexdigest()
                                 if edid else None)
        transform = re.search(r'Transform:\s+([^\n]+)\n\s*([^\n]+)\n\s*([^\n]+)', block)
        values = tuple(float(v) for v in transform.groups()[0].split() +
                       transform.groups()[1].split() + transform.groups()[2].split()) if transform else ()
        output['identity_transform'] = not values or values == (1., 0., 0., 0., 1., 0., 0., 0., 1.)
        output['panning'] = bool(re.search(r'\bpanning\s+[1-9]', block))
    return dict(outputs=outputs, query=query, verbose=verbose,
                display=os.environ.get('DISPLAY'), recorded_at=time.time())


def baseline_output(snapshot):
    active = [o for o in snapshot['outputs'] if o.get('current_mode')]
    if len(active) != 1:
        raise RuntimeError('Low-resolution trial requires exactly one active monitor')
    output = active[0]
    if (output.get('rotation') != 'normal' or not output.get('identity_transform') or
            output.get('panning') or output.get('position') != [0, 0]):
        raise RuntimeError('Existing rotation, transform, panning or offset needs a separate display review')
    if not SAFE_NAME.fullmatch(output['name']) or not SAFE_NAME.fullmatch(output['current_mode']):
        raise RuntimeError('Unexpected display/mode identifier')
    if not re.fullmatch(r'\d+(?:\.\d+)?', output.get('current_rate', '')):
        raise RuntimeError('Current display refresh rate was not identified')
    return output


def restore_command(output):
    return ['xrandr', '--output', output['name'], '--mode', output['current_mode'],
            '--rate', output['current_rate'], '--pos', '0x0', '--rotate', 'normal',
            '--transform', '1,0,0,0,1,0,0,0,1']


def atomic_json(path, data):
    path = Path(path);temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(5))
    with temporary.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush();os.fsync(handle.fileno())
    temporary.replace(path)


class StateLock:
    def __init__(self, directory): self.directory = Path(directory);self.handle = None
    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handle = (self.directory / 'state.lock').open('a+b')
        if fcntl:
            fcntl.flock(self.handle, fcntl.LOCK_EX)
        else:
            import msvcrt
            if self.handle.seek(0, 2) == 0:self.handle.write(b'0');self.handle.flush()
            self.handle.seek(0);msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        return self
    def __exit__(self, *_):
        if fcntl: fcntl.flock(self.handle, fcntl.LOCK_UN)
        else:
            import msvcrt
            self.handle.seek(0);msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


def modeline(size, runner=run):
    width, height = map(int, size.split('x'))
    text = runner(['cvt', str(width), str(height), '60'])
    line = next((line.strip() for line in text.splitlines() if line.strip().startswith('Modeline ')), None)
    if line is None: raise RuntimeError('cvt did not provide a 60 Hz modeline; no display change was made')
    import shlex
    words = shlex.split(line)[1:]
    if len(words) != 12 or not SAFE_NAME.fullmatch(words[0]):
        raise RuntimeError('Unexpected cvt modeline')
    # Recheck requested active dimensions instead of trusting the command label.
    if int(words[2]) != width or int(words[6]) != height:
        raise RuntimeError('cvt active dimensions differ from requested mode')
    words[0] = 'InkSuite_' + size + '_60'
    return words


def launch_watchdog(state_path):
    # Independent process/session survives controller, SSH and GUI termination.
    log = Path(state_path).with_suffix('.watchdog.log').open('ab')
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                    '--state-dir', str(Path(state_path).parent), '_watch',
                                    '--transaction', str(state_path)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   env=dict(os.environ), start_new_session=True, close_fds=True)
    finally: log.close()
    return process.pid


def restore_transaction(path, runner=run, now=None):
    path = Path(path);now = time.monotonic() if now is None else now
    with StateLock(path.parent):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['status'] != 'pending': return data
        data['status'] = 'restoring';atomic_json(path, data)
        try:
            runner(restore_command(baseline_output(data['baseline'])))
            data['status'] = 'rolled_back';data['restored_at_monotonic'] = now
        except Exception as exc:
            data['status'] = 'rollback_failed';data['rollback_error'] = str(exc)
        atomic_json(path, data)
        return data


def watch(path, runner=run, clock=time.monotonic, sleeper=time.sleep):
    path = Path(path)
    # A ready receipt gates mutation: watchdog is actually scheduled before any
    # newmode/addmode/output command can begin.
    atomic_json(path.with_suffix('.ready.json'), dict(pid=os.getpid(), ready=True))
    while True:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['status'] != 'pending': return data
        now = clock();remaining = data['deadline_monotonic'] - now
        if remaining <= 0:
            return restore_transaction(path, runner, clock())
        sleeper(min(.2, remaining))


def trial(size, directory=DEFAULT_STATE, runner=run, guardian=launch_watchdog,
          clock=time.monotonic, ready_wait=True):
    if size not in SUPPORTED_SIZES: raise ValueError('Use an approved 16:9 trial size')
    snapshot = inspect(runner);old = baseline_output(snapshot)
    alias = 'InkSuite_' + size + '_60'
    target = size if size in old['modes'] else alias
    words = modeline(size, runner) if target not in old['modes'] else None
    directory = Path(directory)
    with StateLock(directory):
        for pending in directory.glob('trial-*.json'):
            if pending.name.endswith('.ready.json'): continue
            data = json.loads(pending.read_text(encoding='utf-8'))
            if data.get('status') in ('pending', 'restoring'):
                raise RuntimeError('A display trial is already pending; confirm or roll it back first')
        token = secrets.token_hex(8)
        path = directory / ('trial-' + token + '.json')
        data = dict(schema=1, status='pending', token=token, baseline=snapshot,
                    output=old['name'], edid_sha256=old.get('edid_sha256'),
                    requested_size=size, target_mode=target, target_rate='60',
                    deadline_monotonic=clock() + ROLLBACK_SECONDS,
                    created_at=time.time(), rollback_seconds=ROLLBACK_SECONDS)
        atomic_json(path, data)
        try:
            pid = guardian(path);data['watchdog_pid'] = pid;atomic_json(path, data)
        except Exception as exc:
            data['status'] = 'watchdog_failed';data['watchdog_error'] = str(exc)
            atomic_json(path, data);raise
    # Never hold the transaction lock while awaiting a scheduled watchdog.
    if ready_wait:
        ready_until = clock() + 2.
        while not path.with_suffix('.ready.json').exists():
            if clock() >= ready_until:
                result = restore_transaction(path, runner)
                raise RuntimeError('Rollback watchdog failed to arm: ' + result['status'])
            time.sleep(.02)
    try:
        if words:
            runner(['xrandr', '--newmode'] + words)
            runner(['xrandr', '--addmode', old['name'], target])
        with StateLock(directory):
            current = json.loads(path.read_text(encoding='utf-8'))
            # The bounded output command must finish before the guardian's
            # deadline; do not start a late command that could race rollback.
            if current['status'] != 'pending' or clock() + 6. >= current['deadline_monotonic']:
                raise RuntimeError('Display trial expired before mode switch')
            runner(['xrandr', '--output', old['name'], '--mode', target, '--rate', '60'])
    except Exception:
        restore_transaction(path, runner);raise
    return dict(transaction=str(path), token=token, status='pending', output=old['name'],
                size=size, confirm_before_monotonic=data['deadline_monotonic'],
                rollback_seconds=ROLLBACK_SECONDS,
                message='Confirm only after a person verifies a visible complete picture; otherwise automatic rollback')


def confirm(token, directory=DEFAULT_STATE, runner=run, clock=time.monotonic):
    if not re.fullmatch(r'[a-f0-9]{16}', token): raise ValueError('Invalid confirmation token')
    directory = Path(directory);path = directory / ('trial-' + token + '.json')
    with StateLock(directory):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['status'] != 'pending' or clock() >= data['deadline_monotonic']:
            raise RuntimeError('Display trial is no longer pending; nothing was confirmed')
        current = baseline_output(inspect(runner))
        if (current['name'] != data['output'] or current['current_mode'] != data['target_mode'] or
                current.get('edid_sha256') != data['edid_sha256'] or
                current.get('size') != list(map(int, data['requested_size'].split('x'))) or
                abs(float(current['current_rate']) - 60.) > 1.):
            raise RuntimeError('Display identity/mode changed; nothing was confirmed')
        data['confirmed_at'] = time.time()
        preferred = {key: data[key] for key in ('output', 'edid_sha256', 'requested_size',
                     'target_mode', 'target_rate', 'confirmed_at')}
        preferred.update(schema=1, human_visibility_confirmation=True,
                         source_transaction=path.name)
        atomic_json(directory / 'confirmed.json', preferred)
        data['status'] = 'confirmed';atomic_json(path, data)
        return preferred


def apply_confirmed(directory=DEFAULT_STATE, runner=run, guardian=launch_watchdog,
                    clock=time.monotonic):
    """Restore a previously human-approved preference, guarded until GUI draw.

    No preference means no directory creation and no XRandR command. A changed
    or unidentified monitor is left untouched. Startup failures are reported as
    a fallback result, so an otherwise working 720p application can still open.
    The caller must complete this token only after its first mapped frame.
    """
    directory = Path(directory);preference = directory / 'confirmed.json'
    if not preference.is_file():
        return dict(applied=False, reason='no_saved_preference')
    attempt = None
    try:
        raw = preference.read_bytes();preferred = json.loads(raw)
        if (preferred.get('human_visibility_confirmation') is not True or
                preferred.get('requested_size') not in SUPPORTED_SIZES or
                not preferred.get('edid_sha256')):
            return dict(applied=False, reason='unapproved_or_unidentified_preference')
        current = baseline_output(inspect(runner))
        if (current['name'] != preferred['output'] or
                current.get('edid_sha256') != preferred['edid_sha256']):
            return dict(applied=False, reason='monitor_identity_changed')
        if (current['current_mode'] == preferred['target_mode'] and
                current['size'] == list(map(int, preferred['requested_size'].split('x')))):
            return dict(applied=False, reason='already_selected')
        attempt = trial(preferred['requested_size'], directory, runner, guardian, clock)
        path = Path(attempt['transaction'])
        with StateLock(directory):
            data = json.loads(path.read_text(encoding='utf-8'))
            if data['status'] != 'pending' or preference.read_bytes() != raw:
                raise RuntimeError('Saved preference changed during guarded startup')
            data['kind'] = 'saved_preference_startup'
            data['source_preference_sha256'] = hashlib.sha256(raw).hexdigest()
            atomic_json(path, data)
        return dict(attempt, applied=True, needs_first_frame_completion=True)
    except Exception as exc:
        rollback = (restore_transaction(attempt['transaction'], runner)
                    if attempt else None)
        return dict(applied=False, reason='startup_mode_fallback', error=str(exc),
                    rollback_status=rollback.get('status') if rollback else None)


def complete_confirmed_startup(token, directory=DEFAULT_STATE, runner=run,
                               clock=time.monotonic):
    """Acknowledge first mapped frame for a saved preference, never a new trial."""
    if not re.fullmatch(r'[a-f0-9]{16}', token): raise ValueError('Invalid startup token')
    directory = Path(directory);path = directory / ('trial-' + token + '.json')
    with StateLock(directory):
        data = json.loads(path.read_text(encoding='utf-8'))
        if (data.get('kind') != 'saved_preference_startup' or data['status'] != 'pending' or
                clock() >= data['deadline_monotonic']):
            raise RuntimeError('This token cannot complete a guarded preference startup')
        raw = (directory / 'confirmed.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != data['source_preference_sha256']:
            raise RuntimeError('Saved preference changed; startup remains guarded')
        current = baseline_output(inspect(runner))
        if (current['name'] != data['output'] or current['current_mode'] != data['target_mode'] or
                current.get('edid_sha256') != data['edid_sha256'] or
                current['size'] != list(map(int, data['requested_size'].split('x')))):
            raise RuntimeError('Preferred mode is not active; startup remains guarded')
        data['status'] = 'confirmed_startup';data['first_frame_at'] = time.time()
        atomic_json(path, data)
        return dict(completed=True, status=data['status'], preference_unchanged=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('inspect')
    actions.add_parser('try').add_argument('--size', choices=SUPPORTED_SIZES, default='960x540')
    actions.add_parser('confirm').add_argument('--token', required=True)
    for name in ('rollback', '_watch'):
        actions.add_parser(name).add_argument('--transaction', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'inspect': result = inspect()
    elif args.action == 'try': result = trial(args.size, args.state_dir)
    elif args.action == 'confirm': result = confirm(args.token, args.state_dir)
    else:
        path = args.transaction.resolve()
        if path.parent != args.state_dir.resolve() or not re.fullmatch(r'trial-[a-f0-9]{16}\.json', path.name):
            raise ValueError('Transaction must be an InkSuite trial in the selected state directory')
        result = watch(path) if args.action == '_watch' else restore_transaction(path)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get('status') == 'rollback_failed' else 0


if __name__ == '__main__': raise SystemExit(main())

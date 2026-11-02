"""Read-only board closeout evidence. Never fetches a camera image."""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

R = Path('/home/ubuntu/bodyink-live')


def service(name):
    result = subprocess.run(['systemctl', 'show', name, '-p', 'ActiveState',
                             '-p', 'SubState', '-p', 'MainPID'],
                            capture_output=True, text=True, check=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect-action-release', action='store_true')
    args = parser.parse_args()
    states = {name: service(name+'.service') for name in
              ('bodyink-live', 'bodyink-camera', 'bodyink-dpu',
               'bodyink-batch-recovery', 'bodyink-roi-v3-limited', 'bodyink-roi-v4-limited')}
    errors = []
    for name in ('bodyink-live', 'bodyink-camera', 'bodyink-dpu'):
        if states[name].get('ActiveState') != 'active': errors.append(name+' not active')
    for name in ('bodyink-batch-recovery', 'bodyink-roi-v3-limited', 'bodyink-roi-v4-limited'):
        if states[name].get('ActiveState') in ('active', 'activating', 'deactivating'):
            errors.append(name+' still running')
    status_path = R/'status.json'
    status = json.loads(status_path.read_text())
    runtime = status['runtime']
    model = status.get('model', {})
    expected_model = 'b63f213fdc51ec7efb78a353a6d1e58627470e7cefa0e1cb0b7202d34793f1a9'
    if model.get('model_sha256') != expected_model:
        errors.append('runtime does not report the validated pretrained Hourglass model')
    age = time.time()-status_path.stat().st_mtime
    if not 0 <= age < 15: errors.append('runtime status not fresh')
    if runtime.get('pid') != int(states['bodyink-live'].get('MainPID', '0')):
        errors.append('runtime status PID mismatch')
    if runtime.get('readiness_errors'): errors.append('enabled action readiness errors')
    if runtime.get('enabled_actions') != ['A01', 'A02', 'A03', 'A04', 'A06']:
        errors.append('unexpected enabled actions')
    camera = json.loads(Path('/run/bodyink-camera/status.json').read_text())
    camera_age = time.monotonic()-camera.get('updated_monotonic_seconds', 0)
    if camera.get('state') != 'running' or not 0 <= camera_age < 20:
        errors.append('camera not running with fresh status')
    files = ['action_engine.py', 'app.py', 'ink_renderer.py',
             'dataset/templates.npz', 'dataset/speed_calibration.json']
    if args.expect_action_release:
        files += ['base_action_engine.py', 'continuing_action_engine.py', 'speed_profile.py']
    hashes = {name: hashlib.sha256((R/name).read_bytes()).hexdigest() for name in files}
    if args.expect_action_release:
        release = R/'action-release-v52-0915'
        expected = json.loads((release/'frozen-manifest.json').read_text())['code_and_data_sha256']
        for name in files:
            key = Path(name).name
            if key in expected and hashes[name] != expected[key]: errors.append('hash mismatch '+name)
        receipt = json.loads((release/'installation-result.json').read_text())
        if not receipt.get('installed'): errors.append('missing installation success')
        if not (Path(receipt['backup'])/'manifest.json').exists(): errors.append('missing rollback manifest')
    else:
        receipt = None
    result = dict(passed=not errors, errors=errors, services=states,
                  status_age_seconds=age, camera_status_age_seconds=camera_age,
                  runtime={key: runtime.get(key) for key in
                           ('pid', 'pose_count', 'capture_fps', 'pose_fps', 'enabled_actions',
                            'readiness_errors', 'model_name', 'uptime_seconds')},
                  hashes=hashes, installation=receipt,
                  model_sha256=model.get('model_sha256'),
                  scope='Service and version restoration only; no image capture or live accuracy acceptance.')
    print(json.dumps(result, indent=2))
    if errors: raise SystemExit(1)


if __name__ == '__main__': main()

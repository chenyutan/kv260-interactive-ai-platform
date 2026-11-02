"""Read-only, bounded display diagnostics; never confuse visibility with readiness."""
import json

PROBE = r'''
import glob, json, os, pathlib, re, subprocess
result = {"connectors": [], "session": "unavailable", "visual_verified": False}
for name in sorted(glob.glob('/sys/class/drm/card*-*'))[:8]:
    p = pathlib.Path(name)
    try:
        status = (p/'status').read_text().strip()
        data = (p/'edid').read_bytes()
        valid = (len(data) >= 128 and len(data) % 128 == 0
                 and data[:8] == bytes.fromhex('00ffffffffffff00')
                 and len(data) == 128 * (1 + data[126])
                 and all(sum(data[i:i+128]) % 256 == 0 for i in range(0,len(data),128)))
        result['connectors'].append({'name': p.name, 'status': status,
            'edid_bytes': len(data), 'edid_valid': valid})
    except OSError:
        result['connectors'].append({'name': p.name, 'status': 'unreadable'})
# Only inspect this SSH user's graphical session. Never read another user's
# Xauthority, elevate, or assume that GDM :0 is the desktop.
uid = os.getuid()
auth = pathlib.Path('/run/user')/str(uid)/'gdm/Xauthority'
if auth.is_file():
    for sock in sorted(glob.glob('/tmp/.X11-unix/X*'))[:8]:
        number = pathlib.Path(sock).name[1:]
        if not number.isdigit(): continue
        env = dict(os.environ, DISPLAY=':'+number, XAUTHORITY=str(auth))
        try:
            r = subprocess.run(['xrandr','--current'],env=env,capture_output=True,text=True,timeout=2)
            if r.returncode: continue
            result.update(session='accessible_x11', display=':'+number)
            m = re.search(r'current (\d+) x (\d+)',r.stdout)
            if m: result['active_size'] = [int(m[1]),int(m[2])]
            q = subprocess.run(['xset','q'],env=env,capture_output=True,text=True,timeout=2)
            if q.returncode == 0:
                m = re.search(r'Monitor is (\w+)',q.stdout)
                result['monitor_power'] = m[1].lower() if m else 'unknown'
                result['dpms'] = 'disabled' if 'DPMS is Disabled' in q.stdout else 'enabled' if 'DPMS is Enabled' in q.stdout else 'unknown'
            break
        except (OSError,subprocess.TimeoutExpired): continue
print(json.dumps(result,separators=(',',':')))
'''


def summarize_probe(command: dict) -> dict:
    """Keep raw diagnostics out of the agent response and fail closed."""
    try:
        value = json.loads(command.get("stdout", ""))
        if command.get("returncode") != 0 or not isinstance(value, dict):
            raise ValueError("probe failed")
        if not isinstance(value.get("connectors"), list):
            raise TypeError("missing connectors")
    except (ValueError, TypeError):
        return {"status": "unavailable", "visual_verified": False,
                "issues": ["display_probe_failed"]}
    issues = []
    connected = [c for c in value["connectors"] if isinstance(c, dict)
                 and c.get("status") == "connected"]
    if not connected:
        issues.append("display_not_connected")
    if any(not c.get("edid_valid") for c in connected):
        issues.append("display_edid_missing_or_invalid")
    if value.get("session") != "accessible_x11":
        issues.append("display_session_unavailable")
    if value.get("monitor_power") == "off":
        issues.append("display_power_off")
    value.update(status="review_required", visual_verified=False, issues=issues)
    return value

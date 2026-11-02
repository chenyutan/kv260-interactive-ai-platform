#!/bin/bash
# Local startup gate for the verified KV260 benchmark overlay.
# Follows xilinx_bridge.kv260._check_unload_safety/_xmutil_checked.
# Does not touch the camera, kernel, boot configuration, or bound VCU drivers.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

TARGET=bodyink-usb-pl
STARTER=k26-starter-kits
EXPECTED=0x101000016010407
fail() { printf 'BODYINK_DPU_FAILED: %s\n' "$*" >&2; exit 1; }
log() { printf 'BODYINK_DPU: %s\n' "$*"; }
[[ $(id -u) -eq 0 ]] || fail 'prepare_dpu requires root'
for command in xmutil python3 timeout flock mktemp runuser; do
    command -v "$command" >/dev/null || fail "missing command: $command"
done
[[ -d /sys/bus/platform/devices ]] || fail 'unload_safety_unknown: platform device sysfs unavailable'

exec 9>/run/lock/bodyink-dpu.lock
flock -n 9 || fail 'another BodyInk DPU preparation is already running'
WORK=$(mktemp -d /run/bodyink-dpu.XXXXXX)
trap 'rm -f "$WORK/listapps.txt" "$WORK/operation.txt" "$WORK/query.json" "$WORK/query.err"; rmdir "$WORK"' EXIT

# Parse the actual xmutil listapps columns. Unknown/malformed slot state fails
# closed. The caller receives a newline-separated list of active app names.
active_apps() {
    if ! timeout 15s xmutil listapps >"$WORK/listapps.txt" 2>&1; then
        cat "$WORK/listapps.txt" >&2
        fail 'xmutil listapps failed'
    fi
    python3 - "$WORK/listapps.txt" "$TARGET" "$STARTER" <<'PY'
import pathlib,re,sys
text=pathlib.Path(sys.argv[1]).read_text()
if re.search(r'error|failed|segmentation fault',text,re.I):
    raise SystemExit('xmutil listapps reported an error')
if not re.search(r'Accelerator.*Active_slot',text):
    raise SystemExit('xmutil listapps format unknown')
entries={}
for line in text.splitlines():
    p=line.split()
    if not p or p[0]=='Accelerator': continue
    if len(p)!=6 or p[1]!='XRT_FLAT' or p[3]!='XRT_FLAT':
        raise SystemExit('xmutil listapps row format unknown: '+line)
    if p[0] in entries: raise SystemExit('xmutil listapps duplicate app')
    if p[-1]!='-1' and not re.fullmatch(r'\d+,(?:\d+,)*',p[-1]):
        raise SystemExit('xmutil listapps slot format unknown: '+p[-1])
    entries[p[0]]=p[-1]
if sys.argv[2] not in entries or sys.argv[3] not in entries:
    raise SystemExit('expected installed target/starter app absent')
for name,slot in entries.items():
    if slot!='-1': print(name)
PY
}

unload_safety() {
    local device module
    [[ -d /sys/bus/platform/devices ]] || fail 'unload_safety_unknown: sysfs unavailable'
    for device in /sys/bus/platform/devices/*; do
        [[ -L "$device/driver/module" ]] || continue
        module=$(readlink "$device/driver/module") || fail 'unload_safety_unknown: unreadable module link'
        case "${module##*/}" in
            al5e|al5d|allegro|xlnx_vcu)
                fail "vcu_unload_known_failure: bound ${module##*/} at ${device##*/}; no unload attempted" ;;
        esac
    done
}

checked_operation() {
    local operation=$1
    shift
    if ! timeout 25s xmutil "$operation" "$@" >"$WORK/operation.txt" 2>&1; then
        cat "$WORK/operation.txt" >&2
        fail "xmutil $operation failed or timed out; no retry or forced unload"
    fi
    cat "$WORK/operation.txt"
    python3 - "$WORK/operation.txt" "$operation" "$TARGET" <<'PY'
import pathlib,re,sys
text=pathlib.Path(sys.argv[1]).read_text()
if re.search(r'error|failed|segmentation fault',text,re.I):
    raise SystemExit('xmutil reported failure despite its exit status')
pattern=(r'returns:\s*0\s*\(Ok\)' if sys.argv[2]=='unloadapp'
         else re.escape(sys.argv[3])+r':\s*loaded to slot\s+\d+\b')
if not re.search(pattern,text,re.I):
    raise SystemExit('xmutil positive success evidence absent; refusing to continue')
PY
}

probe() {
    # VART uses per-user /tmp/DPU_* locks. Probe as the same user as the live
    # Runner: a root probe can fail protected_regular or leave root-owned locks.
    if ! timeout 15s runuser -u ubuntu -- env LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" /usr/bin/python3 -m xdputil query >"$WORK/query.json" 2>"$WORK/query.err"; then
        cat "$WORK/query.err" >&2
        fail 'xdputil query failed; keeping the current overlay unchanged'
    fi
    python3 - "$WORK/query.json" "$EXPECTED" <<'PY'
import json,pathlib,sys
data=json.loads(pathlib.Path(sys.argv[1]).read_text())
kernels=data.get('kernels')
if not isinstance(kernels,list) or not kernels:
    raise SystemExit('DPU probe has no kernels')
expected=int(sys.argv[2],0)
for kernel in kernels:
    value=kernel.get('fingerprint')
    actual=value if isinstance(value,int) else int(str(value),0)
    if actual!=expected:
        raise SystemExit('DPU fingerprint mismatch: '+hex(actual))
print('BODYINK_DPU_READY: '+hex(expected)+'; kernels='+str(len(kernels)))
PY
}

apps=$(active_apps) || fail 'active application state unknown'
if [[ "$apps" == "$TARGET" ]]; then
    log 'target already active; read-only fingerprint probe, no xmutil transition'
    probe
    exit 0
fi
[[ "$apps" == "$STARTER" ]] || fail "unsupported active app state: ${apps:-none}; preserving current overlay"

# Only the known starter-kit transition is automated. Re-read state immediately
# before the binding guard to reduce races with a separately operated xmutil.
apps=$(active_apps) || fail 'active application state unknown before transition'
[[ "$apps" == "$STARTER" ]] || fail 'active application changed before transition'
unload_safety
log 'starter-kit is the only active app and no VCU driver is bound; loading verified target once'
checked_operation unloadapp
apps=$(active_apps) || fail 'application state unknown after unload; no load attempted'
[[ -z "$apps" ]] || fail 'an application remains active after unload; no load attempted'
checked_operation loadapp "$TARGET"
apps=$(active_apps) || fail 'application state unknown after load'
[[ "$apps" == "$TARGET" ]] || fail 'target not confirmed as the unique active application'
probe

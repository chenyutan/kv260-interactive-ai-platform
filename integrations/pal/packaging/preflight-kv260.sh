#!/bin/bash
# Read-only: no sudo, apt changes, network, camera, files, or services are modified.
set -uo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BINARY="$ROOT/bin/sdlpal"
DATA=${PAL_DATA_DIR:-"$ROOT/data"}
SAVES=${PAL_SAVE_DIR:-"${XDG_DATA_HOME:-$HOME/.local/share}/pal-kv260/saves"}
while (($#)); do
 case "$1" in
 --data-dir|--save-dir)
  (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
  if [[ "$1" == --data-dir ]]; then DATA=$2; else SAVES=$2; fi; shift 2 ;;
 --help) echo 'preflight-kv260.sh [--data-dir DIR] [--save-dir DIR]'; exit 0 ;;
 *) echo "Unknown option: $1" >&2; exit 2 ;;
 esac
done
errors=0
warn(){ printf 'WARNING: %s\n' "$*"; }
fail(){ printf 'FAIL: %s\n' "$*"; errors=$((errors+1)); }
printf 'Machine: %s\n' "$(uname -m)"
[[ "$(uname -m)" == aarch64 ]] || fail 'This package requires AArch64 Linux.'
[[ -r /etc/os-release ]] && cat /etc/os-release
[[ -x "$BINARY" ]] || fail "Executable missing: $BINARY"
if command -v readelf >/dev/null && [[ -f "$BINARY" ]]; then
 readelf -h "$BINARY" | grep -q AArch64 || fail 'Executable is not AArch64.'
 readelf -d "$BINARY" | sed -n 's/.*Shared library: \[\(.*\)\]/Needs: \1/p'
fi
cache=$(ldconfig -p 2>/dev/null || true)
if ! grep -q 'libSDL-1.2.so.0 ' <<<"$cache"; then
 fail 'SDL 1.2 runtime not found in the system loader cache.'
 echo 'If available for the board OS, install separately: sudo apt-get install libsdl1.2debian'
 echo 'Do not copy the SDK glibc into /lib or /usr/lib.'
 if command -v apt-cache >/dev/null; then apt-cache policy libsdl1.2debian 2>/dev/null; fi
fi
if [[ -z "${DISPLAY:-}" ]]; then warn 'DISPLAY is unset; run from the board graphical session for X11 video.'; fi
if [[ -n "${DISPLAY:-}" ]] && command -v xdpyinfo >/dev/null; then
 xdpyinfo >/dev/null 2>&1 || fail 'DISPLAY exists but this account cannot access the X11 session.'
fi
if [[ -d "$SAVES" ]]; then
 [[ -w "$SAVES" ]] || fail "Save directory is not writable: $SAVES"
else
 warn "Save directory does not exist; normal run-pal.sh launch will create it: $SAVES"
 ancestor=$SAVES
 while [[ ! -e "$ancestor" && "$ancestor" != / && "$ancestor" != . ]]; do ancestor=$(dirname -- "$ancestor"); done
 [[ -d "$ancestor" && -w "$ancestor" ]] || fail "No writable existing ancestor for save directory: $SAVES"
fi
printf 'Data directory: %s\nSave directory: %s\n' "$DATA" "$SAVES"
[[ -d "$DATA" && -r "$DATA" ]] || fail 'Supply a readable directory with your own PAL resources.'
if [[ "$errors" -eq 0 ]]; then
 "$BINARY" --data-dir "$DATA" --save-dir "$SAVES" --check-data || fail 'PAL resource validation failed.'
fi
if [[ "$errors" -eq 0 ]]; then
 echo 'PREFLIGHT_OK: dependency/resource checks passed; use run-pal.sh to prepare any missing save directory. Actual graphics/audio/gameplay still require board testing.'
else
 printf 'PREFLIGHT_FAILED: %d issue(s). Nothing was changed.\n' "$errors"
 exit 1
fi

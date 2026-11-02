#!/bin/bash
# Independent PAL entry point; no InkSuite, audio-service, or system-library changes.
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BINARY="$ROOT/bin/sdlpal"
if [[ ! -x "$BINARY" ]]; then
 echo "Missing game executable: $BINARY" >&2
 echo 'This launcher must run inside the extracted ARM64 release package.' >&2
 exit 2
fi
DATA=${PAL_DATA_DIR:-"$ROOT/data"}
SAVES=${PAL_SAVE_DIR:-"${XDG_DATA_HOME:-$HOME/.local/share}/pal-kv260/saves"}
readonly_request=0
forward=()
while (($#)); do
 case "$1" in
 --data-dir|--save-dir)
  (($# >= 2)) && [[ -n "$2" ]] || { echo "$1 requires a nonempty value" >&2; exit 2; }
  if [[ "$1" == --data-dir ]]; then DATA=$2; else SAVES=$2; fi
  shift 2 ;;
 --help|-h|--version)
  readonly_request=1
  forward+=("$@")
  break ;;
 --check-data|--self-test)
  readonly_request=1; forward+=("$1"); shift ;;
 --size|--self-test-bmp)
  (($# >= 2)) && [[ -n "$2" ]] || { echo "$1 requires a nonempty value" >&2; exit 2; }
  forward+=("$1" "$2"); shift 2 ;;
 --no-audio|--windowed|--fullscreen)
  forward+=("$1"); shift ;;
 *) echo "Unknown option: $1" >&2; exit 2 ;;
 esac
done
# Resolve caller-relative paths before entering the package working directory.
[[ "$DATA" == /* ]] || DATA="$PWD/$DATA"
[[ "$SAVES" == /* ]] || SAVES="$PWD/$SAVES"
if [[ "$readonly_request" -eq 0 ]]; then
 mkdir -p -- "$SAVES"
 [[ -d "$SAVES" && -w "$SAVES" ]] || { echo "Save directory is not writable: $SAVES" >&2; exit 2; }
fi
cd "$ROOT"
# Read-only CLI modes create no save directory. Explicit --self-test-bmp remains
# the binary's requested output; no files or paths are silently manufactured.
exec "$BINARY" --data-dir "$DATA" --save-dir "$SAVES" --windowed --size 640x400 "${forward[@]}"

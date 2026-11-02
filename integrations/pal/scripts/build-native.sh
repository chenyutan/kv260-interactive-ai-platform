#!/bin/bash
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD="$ROOT/build/native-candidate"
mkdir -p "$BUILD" "$ROOT/reports/build" "$ROOT/sdk/tmp"
export TMPDIR="$ROOT/sdk/tmp"
SDL_CONFIG=${SDL_CONFIG:-sdl-config}
command -v "$SDL_CONFIG" >/dev/null || { echo 'SDL 1.2 development files missing. See sdk/README.md.' >&2; exit 2; }
JOBS=${PAL_BUILD_JOBS:-4}
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo 'PAL_BUILD_JOBS must be a positive integer' >&2; exit 2; }
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
make -C "$ROOT/source" -j"$JOBS" app \
 BUILD_DIR="$BUILD" OBJ_DIR="$BUILD/obj" BINARY="$BUILD/sdlpal" \
 CC="${CC:-gcc}" CXX="${CXX:-g++}" LD="${CXX:-g++}" SDL_CONFIG="$SDL_CONFIG" \
 CFLAGS="${CFLAGS:--O2 -g0 -std=gnu11}" CXXFLAGS="${CXXFLAGS:--O2 -g0 -std=gnu++11}" \
 LDFLAGS="${LDFLAGS:-}" CPPFLAGS="${CPPFLAGS:-}" 2>&1 | tee "$ROOT/reports/build/native-$STAMP.log"
file "$BUILD/sdlpal"
sha256sum "$BUILD/sdlpal" > "$BUILD/sdlpal.sha256"

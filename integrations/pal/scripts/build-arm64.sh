#!/bin/bash
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SDK=${PAL_SDK_ROOT:-"$ROOT/sdk"}
BUILD="$ROOT/build/arm64"
for exe in "$SDK/bin/aarch64-linux-gnu-gcc" "$SDK/bin/aarch64-linux-gnu-g++" "$SDK/bin/aarch64-sdl-config"; do
 test -x "$exe" || { echo "Missing SDK executable: $exe" >&2; exit 2; }
done
mkdir -p "$BUILD" "$ROOT/reports/build" "$SDK/tmp"
export TMPDIR="$SDK/tmp"
JOBS=${PAL_BUILD_JOBS:-4}
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo 'PAL_BUILD_JOBS must be a positive integer' >&2; exit 2; }
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
make -C "$ROOT/source" -j"$JOBS" app \
 BUILD_DIR="$BUILD" OBJ_DIR="$BUILD/obj" BINARY="$BUILD/sdlpal" \
 CC="$SDK/bin/aarch64-linux-gnu-gcc" CXX="$SDK/bin/aarch64-linux-gnu-g++" \
 LD="$SDK/bin/aarch64-linux-gnu-g++" SDL_CONFIG="$SDK/bin/aarch64-sdl-config" \
 CFLAGS="${CFLAGS:--O2 -g0 -std=gnu11 -mcpu=cortex-a53}" \
 CXXFLAGS="${CXXFLAGS:--O2 -g0 -std=gnu++11 -mcpu=cortex-a53}" \
 LDFLAGS="${LDFLAGS:-}" CPPFLAGS="${CPPFLAGS:-}" 2>&1 | tee "$ROOT/reports/build/arm64-$STAMP.log"
file "$BUILD/sdlpal"
readelf -h "$BUILD/sdlpal" | grep -q 'AArch64' || { echo 'Wrong output architecture' >&2; exit 3; }
readelf -d "$BUILD/sdlpal" > "$BUILD/dynamic-dependencies.txt"
readelf --version-info "$BUILD/sdlpal" > "$BUILD/symbol-versions.txt"
sha256sum "$BUILD/sdlpal" > "$BUILD/sdlpal.sha256"

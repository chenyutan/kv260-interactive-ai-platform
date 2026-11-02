#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Static libc avoids Ubuntu 24.04 cross compiler / board glibc 2.35 mismatch.
"${CC:-aarch64-linux-gnu-gcc-11}" -std=c11 -O2 -Wall -Wextra -Wconversion -Wshadow -Wformat=2 -Werror -static "$here/bodyink-camera.c" -o "$here/bodyink-camera.aarch64"
sha256sum "$here/bodyink-camera.aarch64"

#!/bin/sh
# Install a reviewed, camera-configured checkout's two privileged startup helpers.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
test "$(id -u)" -eq 0 || { echo 'Root is required for installation.' >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")/../bodyink-live" && pwd)
if grep -Fq '__BODYINK_CAMERA_SERIAL__' "$here/recover_camera.sh"; then
    echo 'Run camera/configure_capture.py for this camera before installation.' >&2
    exit 1
fi
for dir in / /usr /usr/local /usr/local/libexec /usr/local/libexec/bodyink; do
    if test -L "$dir"; then
        echo "Unsafe helper directory (symlink): $dir" >&2; exit 1
    fi
    if ! test -d "$dir"; then
        install -d -o root -g root -m 0755 "$dir"
    fi
    mode=$(stat -c '%a' "$dir")
    if test "$(stat -c '%u' "$dir")" -ne 0 || test "$((0$mode & 022))" -ne 0; then
        echo "Expected root ownership and no group/other writes: $dir" >&2; exit 1
    fi
done
for script in prepare_dpu.sh recover_camera.sh; do
    dest=/usr/local/libexec/bodyink/$script
    test ! -L "$dest" || { echo "Refusing symlink: $dest" >&2; exit 1; }
    install -o root -g root -m 0755 "$here/$script" "$dest"
done

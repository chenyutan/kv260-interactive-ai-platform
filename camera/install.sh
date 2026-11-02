#!/bin/sh
# Run on the board after the permanent patched kernel has passed its boot test.
set -eu
case "${1:-}" in
    ""|--install-only) ;;
    *) echo "Usage: $0 [--install-only]" >&2; exit 2 ;;
esac
test "$(id -u)" = 0 || { echo 'Run as root' >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test -f /sys/module/uvcvideo/parameters/bodyink_ss_bandwidth || { echo 'Boot the supported BodyInk kernel first' >&2; exit 1; }
test -z "$(systemctl show -p MainPID --value bodyink-camera.service 2>/dev/null | sed '/^0$/d')" || { echo 'Stop existing bodyink-camera.service before upgrading' >&2; exit 1; }
id ubuntu >/dev/null
getent group video >/dev/null
install -d -m 0755 /usr/local/libexec
install -m 0755 "$here/bodyink-camera.aarch64" /usr/local/bin/bodyink-camera
install -m 0755 "$here/bodyink-enable-bandwidth" /usr/local/libexec/bodyink-enable-bandwidth
install -m 0755 "$here/snapshot.py" /usr/local/bin/bodyink-camera-snapshot
install -m 0644 "$here/bodyink-camera.service" /etc/systemd/system/bodyink-camera.service
install -m 0644 "$here/bodyink-uvc-bandwidth.service" /etc/systemd/system/bodyink-uvc-bandwidth.service
if test ! -e /etc/default/bodyink-camera; then
    install -m 0644 "$here/bodyink-camera.default" /etc/default/bodyink-camera
fi
systemctl daemon-reload
systemctl enable bodyink-uvc-bandwidth.service bodyink-camera.service
if test "${1:-}" = --install-only; then
    exit 0
fi
systemctl start bodyink-camera.service
systemctl --no-pager status bodyink-camera.service

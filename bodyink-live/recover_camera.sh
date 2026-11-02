#!/bin/sh
set -eu
device=/dev/v4l/by-id/usb-Generic_USB_Camera3___BODYINK_CAMERA_SERIAL__-video-index0
test -c "$device" || exit 0
# Respect an explicitly disabled capture service. Never restart a running owner.
systemctl is-enabled --quiet bodyink-camera.service || exit 0
# Refresh the retry budget even if the old disconnected owner has not exited
# yet. `start` is idempotent and leaves an active capture process untouched.
systemctl reset-failed bodyink-camera.service
# A device-add notification can arrive during boot ordering; do not block udev
# or create a synchronous dependency on camera startup here.
systemctl start --no-block bodyink-camera.service

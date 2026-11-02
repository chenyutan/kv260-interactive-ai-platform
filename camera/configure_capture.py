"""Bind the source checkout's USB camera templates to a user-selected KV260 camera."""
import argparse
from pathlib import Path
import re

FILES = (
    "bodyink-live/bodyink-camera-device.conf", "bodyink-live/camera_link.py",
    "bodyink-live/camera_settings.py", "bodyink-live/recover_camera.sh",
    "camera/91-bodyink-camera-recover.rules", "camera/bodyink-camera-device.conf",
    "camera/bodyink-camera.c", "camera/bodyink-camera.default",
    "systemd/bodyink-camera.service.d/device.conf",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", type=Path, help="Selected video capture node on the target, e.g. /dev/video0")
    args = parser.parse_args()
    node = args.device.resolve(strict=True)
    if node.parent != Path("/dev") or not re.fullmatch(r"video[0-9]+", node.name):
        parser.error("expected a V4L video node")
    video = Path("/sys/class/video4linux") / node.name
    if (video / "index").read_text().strip() != "0":
        parser.error("select index 0 of the capture device")
    usb = next((p for p in (video / "device").resolve().parents if (p / "idVendor").exists() and (p / "idProduct").exists()), None)
    if usb is None or (usb / "idVendor").read_text().strip() != "0bda" or (usb / "idProduct").read_text().strip() != "5858":
        parser.error("this integration is configured for the 0bda:5858 USB capture model")
    serial = (usb / "serial").read_text().strip()
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", serial):
        parser.error("unexpected USB serial format")
    checkout = Path(__file__).resolve().parents[1]
    token = "__BODYINK_CAMERA_SERIAL__"
    files = [(checkout / name, (checkout / name).read_text(encoding="utf-8")) for name in FILES]
    if any(token not in content and serial not in content for _, content in files):
        parser.error("target checkout already binds another device; start from a clean checkout")
    for path, content in files:
        if token in content:
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content.replace(token, serial))
    print("Updated target source checkout. Build the camera and install its unit/drop-in and udev rule on the KV260.")


if __name__ == "__main__":
    main()

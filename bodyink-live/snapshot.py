#!/usr/bin/env python3
"""Request an immutable YUYV frame without opening/stealing the camera.

Python's os.pread is intentional: the received memfd shares the sender's file
offset, which is at EOF after population. Only the new sealed fd is consumed.
"""
import argparse
import array
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import mmap


def capture_snapshot(path="/run/bodyink-camera/snapshot.sock", timeout=4.0, *, copy=True):
    """Return immutable bytes by default, or an owned read-only mapping.

    With copy=False the mapping owns its lifetime independently of the received
    descriptor. Do not explicitly close it while queued consumers still use it.
    NumPy buffer views keep their own reference until native processing ends.
    """
    descriptors = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(timeout)
        connection.connect(path)
        payload, ancillary, flags, _ = connection.recvmsg(4096, socket.CMSG_SPACE(16), socket.MSG_CMSG_CLOEXEC)
        received_at = time.monotonic()
        try:
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    values = array.array("i")
                    values.frombytes(data[:len(data) - len(data) % values.itemsize])
                    descriptors.extend(values)
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                raise RuntimeError("Truncated snapshot response")
            if not payload:
                raise ConnectionError("Snapshot service closed the connection before delivering a frame")
            metadata = json.loads(payload)
            if "error" in metadata:
                raise RuntimeError(metadata["error"])
            if len(descriptors) != 1:
                raise RuntimeError("Expected exactly one frame descriptor")
            width, height = metadata.get('width'), metadata.get('height')
            if (width,height) not in ((1920,1080),(1280,720),(960,540)):
                raise RuntimeError("Unsupported snapshot resolution")
            expected = dict(schema_version=1, width=width, height=height,
                            stride=width*2, fourcc="YUYV", bytes=width*height*2,
                            sealed_memfd=True)
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise RuntimeError("Snapshot format mismatch")
            descriptor = descriptors[0]
            seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
            required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK
            if seals & required != required or os.fstat(descriptor).st_size != metadata["bytes"]:
                raise RuntimeError("Frame is not immutable or size does not match")
            read_started = time.monotonic()
            frame = (os.pread(descriptor, metadata["bytes"], 0) if copy else
                     mmap.mmap(descriptor, metadata['bytes'], flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ))
            read_finished = time.monotonic()
            if len(frame) != metadata["bytes"]:
                raise RuntimeError("Incomplete snapshot frame")
            metadata['_snapshot_trace'] = {'socket_received':received_at,
                'read_started':read_started,'read_finished':read_finished}
            metadata['buffer_mode'] = 'bytes' if copy else 'readonly_mmap'
            return metadata, frame
        finally:
            for descriptor in descriptors:
                os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="/run/bodyink-camera/snapshot.sock")
    parser.add_argument("--timeout", type=float, default=4.0, help="Connection/response timeout in seconds (default: 4)")
    parser.add_argument("--raw", type=Path, help="Save one requested YUYV frame (never a continuous recording)")
    parser.add_argument("--png", type=Path, help="Save one PNG; requires numpy and opencv-python")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    metadata, frame = capture_snapshot(args.socket, timeout=args.timeout)
    metadata["sha256"] = hashlib.sha256(frame).hexdigest()
    if args.raw:
        with args.raw.open("xb") as output:
            output.write(frame)
    if args.png:
        import cv2
        import numpy as np
        if args.png.exists():
            raise FileExistsError(args.png)
        pixels = np.frombuffer(frame, dtype=np.uint8).reshape(metadata['height'], metadata['width'], 2)
        bgr = cv2.cvtColor(pixels, cv2.COLOR_YUV2BGR_YUY2)
        success, encoded = cv2.imencode(".png", bgr)
        if not success:
            raise RuntimeError("PNG encode failed")
        with args.png.open("xb") as output:
            output.write(encoded)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

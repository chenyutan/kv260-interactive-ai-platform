"""Linux-only test peer for replay sizes accepted by the snapshot reader."""
import array
import fcntl
import json
import os
import socket
import sys
from pathlib import Path


def producer_command(directory, mode):
    if not mode:
        # Built from selftest.c, which includes the unmodified production sender.
        return [os.environ.get('BODYINK_TEST_PRODUCER', str(Path(__file__).with_name('selftest-resolution'))), directory]
    return [sys.executable, __file__, directory, mode]


def send_replay(directory, mode):
    width, height = {'720': (1280, 720), '540': (960, 540)}[mode]
    size = width * height * 2
    header = dict(schema_version=1, width=width, height=height, stride=width*2,
                  fourcc='YUYV', bytes=size, sequence=42, sealed_memfd=True,
                  flags=0x2000, timestamp_seconds=123.456789)
    with os.fdopen(os.memfd_create('test-yuyv', os.MFD_ALLOW_SEALING), 'wb') as frame:
        frame.write((bytes(range(251))*((size+250)//251))[:size])
        frame.flush()
        fcntl.fcntl(frame, fcntl.F_ADD_SEALS, fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK |
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SEAL)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
            server.bind(str(Path(directory)/'snapshot.sock'))
            server.listen()
            server.settimeout(5)
            client, _ = server.accept()
            with client:
                client.sendmsg([json.dumps(header).encode()], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                               array.array('i', [frame.fileno()]))])


if __name__ == '__main__':
    send_replay(*sys.argv[1:])

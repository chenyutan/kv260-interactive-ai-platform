"""Bounded RGB transport used only while the camera tool is visible."""
import math
import struct

HEADER=struct.Struct('!4sIIIQd')
# The backend's RGB header carries frame age in milliseconds.
MAX_FRAME_AGE_MS=750.0


def fresh_frame(frame):
    return frame[-1]<=MAX_FRAME_AGE_MS


def read_exact(stream,length):
    parts=[];received=0
    while received<length:
        chunk=stream.read(length-received)
        if not chunk:raise EOFError('Camera preview stream ended')
        parts.append(chunk);received+=len(chunk)
    return b''.join(parts)


def read_frame(stream):
    """Return RGB bytes, dimensions, sequence and age in milliseconds."""
    magic,w,h,length,version,age=HEADER.unpack(read_exact(stream,HEADER.size))
    if magic!=b'BRGB' or not (1<=w<=640 and 1<=h<=360 and length==w*h*3 and math.isfinite(age) and age>=0):
        raise ValueError('Invalid bounded RGB preview')
    return read_exact(stream,length),w,h,version,age

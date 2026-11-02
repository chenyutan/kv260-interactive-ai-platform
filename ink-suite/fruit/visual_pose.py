"""Display-only whole-fruit poses and fixed source-image rotation pivots.

World angle, RNG, trajectories, collisions, half-fruit physics and scoring are
never changed here. Call ensure_visual_birth immediately after a new object is
spawned; the engine's dict-based cut event then inherits the metadata naturally.
Use SourcePivotCache on immutable, final-scale source tiles, before rotation or
transparent cropping. Clear it when rebuilding those sources on resize.
"""
import math
import sys

WHOLE_MAX_DEGREES = 30
WHOLE_STEP_DEGREES = 2
WHOLE_STEPS = 31
HALF_STEPS = 24
INITIAL_TILT_DEGREES = 6
WORLD_ANGLE_GAIN = .15
BIRTH_ANGLE_KEY = '_visual_birth_angle'
_MAX_ANGLE = math.radians(WHOLE_MAX_DEGREES)
_INITIAL_TILT = math.radians(INITIAL_TILT_DEGREES)


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Visual angles must be finite numbers') from None
    if not math.isfinite(result):
        raise ValueError('Visual angles must be finite numbers')
    return result


def ensure_visual_birth(obj):
    """Add only the birth-angle key once; do not rewrite world angle or RNG.

    Returns the stored world angle in radians. Missing 'angle' is a caller
    error. Validate before writing so invalid input cannot leave partial state.
    """
    if BIRTH_ANGLE_KEY not in obj:
        birth = _finite(obj['angle'])
        obj[BIRTH_ANGLE_KEY] = birth
        return birth
    return _finite(obj[BIRTH_ANGLE_KEY])


def visual_angle(world_angle, birth_angle):
    """Continuous display angle in radians, bounded to +/-30 degrees.

    The initial phase maps smoothly and periodically to +/-6 degrees. World
    angle is intentionally NOT wrapped: crossing a complete turn must not
    introduce an artificial axis/angle jump. World angular motion is scaled .15.
    """
    world, birth = _finite(world_angle), _finite(birth_angle)
    angle = _INITIAL_TILT * math.sin(birth) + WORLD_ANGLE_GAIN * (world - birth)
    return max(-_MAX_ANGLE, min(_MAX_ANGLE, angle))


def object_visual_angle(obj):
    """Read the whole-fruit display angle, initializing metadata if necessary.

    The normal integration initializes births in tick, not lazily at draw; this
    fallback supports an already-existing object without touching engine state.
    """
    birth = ensure_visual_birth(obj)
    return visual_angle(obj['angle'], birth)


def whole_step(angle):
    """Quantize a DISPLAY angle in radians to a stable integer in [0,30]."""
    degrees = max(-WHOLE_MAX_DEGREES,
                  min(WHOLE_MAX_DEGREES, math.degrees(_finite(angle))))
    # Half-up selection avoids Python's alternating round-to-even tie cases.
    return max(0, min(WHOLE_STEPS - 1,
                      int(math.floor((degrees + WHOLE_MAX_DEGREES) /
                                     WHOLE_STEP_DEGREES + .5))))


def whole_angle(step):
    """Return the cached display angle (radians) for a whole-fruit step."""
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < WHOLE_STEPS:
        raise ValueError('Whole-fruit pose step must be an integer in [0,30]')
    return math.radians(step * WHOLE_STEP_DEGREES - WHOLE_MAX_DEGREES)


def alpha_centroid_argb32(data, width, height, stride, byteorder=sys.byteorder):
    """Fixed alpha-weighted pivot in Cairo source coordinates, no image edit.

    Cairo native-endian ARGB32 is BGRA on little-endian hosts and ARGB on big-
    endian hosts. Only alpha contributes; transparent nonzero RGB and row
    padding are ignored. Pixel centres are x+.5/y+.5. An all-transparent or
    empty source uses its geometric centre.
    """
    if (any(isinstance(v, bool) or not isinstance(v, int)
            for v in (width, height, stride)) or
            width < 0 or height < 0 or stride < width * 4):
        raise ValueError('Invalid ARGB32 shape or stride')
    if byteorder not in ('little', 'big'):
        raise ValueError('Unknown byte order')
    try:
        view = memoryview(data).cast('B')
    except (TypeError, ValueError):
        raise ValueError('ARGB32 data must expose a contiguous byte buffer') from None
    required = (height - 1) * stride + width * 4 if width and height else 0
    if len(view) < required:
        raise ValueError('ARGB32 buffer is shorter than its visible pixel span')
    if not width or not height:
        return width / 2., height / 2.
    alpha_offset = 3 if byteorder == 'little' else 0
    total = xsum = ysum = 0
    for y in range(height):
        row = view[y * stride + alpha_offset:y * stride + width * 4:4]
        row_total = sum(row)
        total += row_total
        ysum += y * row_total
        xsum += sum(x * a for x, a in enumerate(row))
    if not total:
        return width / 2., height / 2.
    return xsum / total + .5, ysum / total + .5


class SourcePivotCache:
    """One immutable-source pivot calculation per surface, until clear().

    Strong references prevent Python object-id reuse. This small source cache
    belongs to one renderer; never feed it angle variants or rotated crops.
    """
    def __init__(self):
        self._sources = {}

    def clear(self):
        self._sources.clear()

    def __len__(self):
        return len(self._sources)

    def get(self, surface):
        key = id(surface)
        entry = self._sources.get(key)
        if entry is not None and entry[0] is surface:
            return entry[1]
        # Cairo FORMAT_ARGB32 has public enum value zero. Keeping this module
        # free of a pycairo import permits byte-layout and engine unit tests.
        if surface.get_format() != 0:
            raise ValueError('Fixed alpha pivot requires Cairo FORMAT_ARGB32')
        surface.flush()
        pivot = alpha_centroid_argb32(surface.get_data(), surface.get_width(),
                                     surface.get_height(), surface.get_stride())
        self._sources[key] = surface, pivot
        return pivot

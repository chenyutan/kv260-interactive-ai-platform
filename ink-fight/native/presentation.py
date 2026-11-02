"""Optional reuse of Suite's native final RGB24 -> RGB565 presenter.

Fight always composes in RGB24. A missing Suite/helper, unsupported CPU or
unmatched destination keeps the original Cairo path, including standalone use.
No GTK application or Sword gameplay module is imported here.
"""
import importlib.util
from pathlib import Path

try:
    import cairo
except ImportError:
    cairo = None


def target_needs_565(target, visual):
    if cairo is None:
        return False
    if isinstance(target, cairo.ImageSurface):
        return target.get_format() == cairo.FORMAT_RGB16_565
    return bool(visual is not None and visual.get_depth() == 16 and
        tuple(visual.get_red_pixel_details()) == (0xf800, 11, 5) and
        tuple(visual.get_green_pixel_details()) == (0x07e0, 5, 6) and
        tuple(visual.get_blue_pixel_details()) == (0x001f, 0, 5))


def load_native_presenter(helper_dir=None):
    """One optional, explicit install path; never search/import a Sword app."""
    root = Path(helper_dir) if helper_dir is not None else Path.home()/'ink-suite/sword'
    try:
        source = root/'rgb565.py'
        if not source.is_file():
            return None
        spec = importlib.util.spec_from_file_location('_inkfight_optional_rgb565', source)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        presenter = module.RGB565Presenter(root)
        return presenter if presenter.enabled else None
    except Exception:
        # Optional display acceleration must not prevent standalone launch.
        # RGB565Presenter also validates architecture, library ABI and buffers.
        return None


class FramePresenter:
    def __init__(self, helper_dir=None):
        self._native = load_native_presenter(helper_dir)

    @property
    def backend(self):
        return self._native.backend if self._native is not None else 'cairo'

    @property
    def calls(self):
        return self._native.calls if self._native is not None else 0

    @property
    def fallback_calls(self):
        return self._native.fallback_calls if self._native is not None else 0

    def convert(self, surface, target, visual=None):
        if self._native is not None and target_needs_565(target, visual):
            return self._native.convert(surface)
        return surface

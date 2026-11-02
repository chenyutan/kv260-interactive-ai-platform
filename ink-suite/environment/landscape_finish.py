"""Source-aware mineral wash for the existing fixed ink network.

This is a finishing pass, not neural super-resolution. It does not introduce
motifs, replace the sky, crop the composition or add a signature to a photo.
"""
import numpy as np
from PIL import Image, ImageFilter

RESAMPLE = getattr(Image, 'Resampling', Image)
VERSION = 'landscape-mineral-wash-v2-hd'


def _rgb(value):
    if (not isinstance(value, np.ndarray) or value.dtype != np.uint8
            or value.ndim != 3 or value.shape[2] != 3
            or min(value.shape[:2]) < 8):
        raise ValueError('Expected an RGB uint8 image, at least 8 pixels per side')


def finish_landscape(source, neural, style='color', long_side=1920):
    """Return a deterministic paper/ink result, preserving source aspect ratio.

    The network supplies the painting's shapes and brush marks. Source color
    contributes only broad, blurred hue regions; no photograph is pasted back.
    The output may exceed inference size, but never source size or 1920px.
    """
    _rgb(source)
    _rgb(neural)
    if style not in ('color', 'ink'):
        raise ValueError('Style must be color or ink')
    if (not isinstance(long_side, int) or isinstance(long_side, bool)
            or not 64 <= long_side <= 1920):
        raise ValueError('Finishing long side must be between 64 and 1920')
    height, width = source.shape[:2]
    scale = min(1., long_side / max(width, height))
    size = (max(8, round(width * scale)), max(8, round(height * scale)))
    painted = Image.fromarray(neural).resize(size, RESAMPLE.BICUBIC)
    rgb = np.asarray(painted, dtype=np.float32) / 255.
    luminance = rgb @ np.array([.2126, .7152, .0722], np.float32)
    density = np.clip(1 - luminance, 0, 1)
    # Curve deepens medium ink, while bright paper and dense black retain range.
    density += .22 * density * (1-density) * np.clip((density-.06)/.24, 0, 1)
    density = np.clip(density, 0, 1)
    paper = np.array([.985, .976, .949], np.float32)
    neutral_ink = np.array([.16, .17, .16], np.float32)
    # Gray neural marks remain recognizable; only a small amount of warm paper.
    base = paper[None, None, :] * (1-density[..., None])
    base += neutral_ink[None, None, :] * density[..., None] * .27
    if style == 'color':
        low_size = (max(8, size[0]//18), max(8, size[1]//18))
        broad = Image.fromarray(source).resize(low_size, RESAMPLE.BILINEAR)
        broad = broad.filter(ImageFilter.GaussianBlur(.8)).resize(size, RESAMPLE.BILINEAR)
        hsv = np.asarray(broad.convert('HSV'), np.float32) / 255.
        hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        cold = np.clip((hue-.16)/.15, 0, 1) * np.clip((.83-hue)/.13, 0, 1)
        blue = np.clip((hue-.43)/.18, 0, 1)
        malachite = np.array([.37, .57, .45], np.float32)
        azurite = np.array([.34, .52, .63], np.float32)
        ochre = np.array([.70, .52, .32], np.float32)
        cinnabar = np.array([.69, .36, .28], np.float32)
        cold_ink = malachite * (1-blue[..., None]) + azurite * blue[..., None]
        red = np.clip((.10-hue)/.08, 0, 1)
        warm_ink = ochre * (1-red[..., None]) + cinnabar * red[..., None]
        pigment = cold[..., None]*cold_ink + (1-cold[..., None])*warm_ink
        # White cloud/sky is paper, not a blanket blue wash. Dense strokes stay ink.
        mid = np.clip((density-.035)/.23, 0, 1) * np.clip((.96-density)/.38, 0, 1)
        amount = .48 * np.clip((saturation-.035)/.38, 0, 1) * mid
        amount *= np.clip((value-.08)/.28, 0, 1)
        base = base*(1-amount[..., None]) + pigment*amount[..., None]
    # Fine, low-amplitude paper variation; deterministic and without image noise.
    yy, xx = np.ogrid[:size[1], :size[0]]
    grain = (.0016*np.sin(xx*1.71 + yy*.43)
             + .0013*np.sin(xx*.37 - yy*1.93)).astype(np.float32)
    base += grain[..., None] * (1-density[..., None])
    return np.rint(np.clip(base, 0, 1)*255).astype(np.uint8)

"""Finish the accepted neural ink draft with deeper ink and mineral-color wash.

The source photograph contributes only heavily downsampled hue regions. All
form, luminance and detail come from the neural painting; no photographic face
or generated reference pixels are pasted into the result.
"""
import numpy as np
from PIL import Image, ImageFilter

RESAMPLE = getattr(Image, 'Resampling', Image)
VERSION = 'figure-mineral-ink-v1'


def finish_ink(source, neural, ink_strength=.32, wash_strength=.85):
    if (source.dtype != np.uint8 or neural.dtype != np.uint8
            or source.ndim != 3 or neural.ndim != 3
            or source.shape[2] != 3 or neural.shape[2] != 3):
        raise ValueError('Ink finishing requires two RGB uint8 images')
    height, width = neural.shape[:2]
    low = Image.fromarray(source).resize((max(16,width//8),max(16,height//8)),RESAMPLE.BILINEAR)
    low = low.filter(ImageFilter.GaussianBlur(1.)).resize((width,height),RESAMPLE.BILINEAR)
    hue, saturation, value = (np.asarray(low.convert('HSV'),np.float32)/255.).transpose(2,0,1)
    rgb = neural.astype(np.float32)/255.
    luminance = rgb @ np.array([.2126,.7152,.0722],np.float32)
    density = np.clip(.97-luminance,0,1)
    # A smooth density curve deepens drawn masses; very pale paper stays pale.
    boost = ink_strength*density*(1-density)*np.clip((density-.06)/.30,0,1)
    rgb *= (np.maximum(0,luminance-boost)/np.maximum(luminance,1e-6))[...,None]
    cool = np.clip((hue-.20)/.16,0,1)*np.clip((.86-hue)/.15,0,1)
    blue_amount = np.clip((hue-.50)/.16,0,1)*.65
    malachite = np.array([.22,.52,.40],np.float32)
    azurite = np.array([.18,.43,.57],np.float32)
    ochre = np.array([.66,.46,.25],np.float32)
    cold_pigment = malachite*(1-blue_amount[...,None])+azurite*blue_amount[...,None]
    pigment = cool[...,None]*cold_pigment+(1-cool[...,None])*ochre
    amount = np.clip((saturation-.04)/.24,0,1)*np.clip((value-.15)/.35,0,1)
    amount *= wash_strength*np.clip((luminance-.08)/.85,0,1)**1.6
    # Preserve dense ink instead of turning black strokes into pastel paint.
    output = rgb*(1-amount[...,None])+pigment*amount[...,None]
    return np.rint(np.clip(output,0,1)*255).astype(np.uint8)

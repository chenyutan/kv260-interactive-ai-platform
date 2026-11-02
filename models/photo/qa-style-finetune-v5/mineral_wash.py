"""Experimental low-frequency mineral pigment over an actual neural ink image.

Source RGB contributes hue regions only. It is never blended as photographic
luminance, detail, or a face patch. No generated reference pixels are composited.
"""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter
RESAMPLE = getattr(Image, 'Resampling', Image)


def mineral_wash(source, ink, strength=.6):
    # Deliberately throw away photographic detail before selecting pigment.
    size = ink.size
    low = source.convert('RGB').resize((max(16, size[0]//8), max(16, size[1]//8)), RESAMPLE.BILINEAR)
    low = low.filter(ImageFilter.GaussianBlur(1.0)).resize(size, RESAMPLE.BILINEAR)
    hsv = np.asarray(low.convert('HSV'), dtype=np.float32) / 255.
    hue, saturation, value = hsv.transpose(2,0,1)
    rgb = np.asarray(ink.convert('RGB'), dtype=np.float32) / 255.
    gray = rgb @ np.array([.2126,.7152,.0722], np.float32)
    # Blue/green photo regions become a malachite/azurite wash, warm regions ochre.
    cool = np.clip((hue-.20)/.16,0,1) * np.clip((.86-hue)/.15,0,1)
    cool_color = np.array([.29,.56,.49],np.float32)
    warm_color = np.array([.69,.50,.29],np.float32)
    pigment = cool[...,None]*cool_color + (1-cool[...,None])*warm_color
    amount = np.clip((saturation-.04)/.24,0,1)*np.clip((value-.15)/.35,0,1)
    amount *= strength * np.clip((gray-.08)/.85,0,1)**1.6
    painted = rgb*(1-amount[...,None]) + pigment*amount[...,None]
    return Image.fromarray(np.rint(np.clip(painted,0,1)*255).astype(np.uint8))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path);parser.add_argument('ink',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--strength',type=float,default=.6)
    args=parser.parse_args()
    mineral_wash(Image.open(args.source),Image.open(args.ink),args.strength).save(args.output)

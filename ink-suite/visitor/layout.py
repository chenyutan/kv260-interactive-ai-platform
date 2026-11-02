"""Pixel-aligned QR geometry shared by native rendering and offline QA."""
import math

def qr_box(width,height,modules):
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (width,height,modules)):
        raise ValueError('Invalid QR layout dimensions')
    # Lower row ends above the menu notice. Whole screen pixels per module
    # preserve the quiet border even at a 640 x 360 internal render size.
    available=min(height*.35,width*.29)
    pitch=max(1,int(available//modules));side=pitch*modules
    return dict(x=round(width*.355+(width*.29-side)/2),y=round(height*.56),side=side,pitch=pitch)

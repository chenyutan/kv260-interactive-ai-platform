"""Persistent Cairo surface, updated only in changed source rectangles."""
import math
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk

class AnimatedArtwork(Gtk.DrawingArea):
 def __init__(self,image):
  super().__init__()
  self.width,self.height=image.size
  self.surface=cairo.ImageSurface(cairo.FORMAT_RGB24,self.width,self.height)
  self.set_size_request(240,180);self.set_hexpand(True);self.set_vexpand(True)
  self.connect('draw',self.draw_art)
  self.apply_patch((0,0,self.width,self.height),image)

 def set_image(self,image):
  if image.size!=(self.width,self.height):
   self.width,self.height=image.size
   self.surface=cairo.ImageSurface(cairo.FORMAT_RGB24,self.width,self.height)
  self.apply_patch((0,0,self.width,self.height),image)
  self.queue_draw()

 def transform(self):
  w,h=self.get_allocated_width(),self.get_allocated_height()
  scale=min(w/self.width,h/self.height)
  return scale,(w-self.width*scale)/2,(h-self.height*scale)/2

 def apply_patch(self,box,image):
  pixels=bytearray(image.convert('RGB').tobytes('raw','BGRX'))
  source=cairo.ImageSurface.create_for_data(pixels,cairo.FORMAT_RGB24,image.width,image.height,image.width*4)
  cr=cairo.Context(self.surface);cr.set_operator(cairo.OPERATOR_SOURCE)
  cr.rectangle(box[0],box[1],image.width,image.height);cr.clip()
  cr.set_source_surface(source,box[0],box[1]);cr.paint();self.surface.flush()
  scale,ox,oy=self.transform()
  self.queue_draw_area(math.floor(ox+box[0]*scale)-2,math.floor(oy+box[1]*scale)-2,math.ceil(image.width*scale)+4,math.ceil(image.height*scale)+4)

 def draw_art(self,widget,cr):
  scale,ox,oy=self.transform()
  cr.save();cr.translate(ox,oy);cr.scale(scale,scale)
  cr.set_source_surface(self.surface,0,0);cr.paint();cr.restore()
  return False

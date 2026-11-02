"""Bound the artwork viewer's presentation; preserve its full saved image."""
import cairo
from render_policy import RenderPolicy, present

def bind_artwork_policy(art):
    policy=RenderPolicy.from_environment()
    original=art.draw_art
    art.disconnect_by_func(original)
    buffer=None
    def draw(widget,target):
        nonlocal buffer
        layout=policy.layout(max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height()))
        w,h=layout.render_size
        if layout.render_size==layout.output_size:
            return original(widget,target)
        if buffer is None or (buffer.get_width(),buffer.get_height())!=(w,h):
            buffer=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        context=cairo.Context(buffer)
        context.set_source_rgb(.965,.952,.920);context.paint()
        scale=min(w/art.width,h/art.height)
        context.translate((w-art.width*scale)/2,(h-art.height*scale)/2)
        context.scale(scale,scale);context.set_source_surface(art.surface,0,0)
        context.get_source().set_filter(cairo.FILTER_NEAREST);context.paint()
        present(target,buffer,layout,background=(.965,.952,.920))
        art._suite_render_layout=layout.as_dict()
        return False
    art.connect('draw',draw)
    art._suite_render_draw=draw
    return draw
